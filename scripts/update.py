import argparse
import sys
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from typing import Union
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import pdist


def parse_args() -> Union[argparse.Namespace, SimpleNamespace]:
    """
    Parse arguments, either from command-line or from interactive input.

    Returns
    -------
    Union[argparse.Namespace, SimpleNamespace]
        A namespace object containing the arguments.
    """

    # If the script is called with no arguments, get them interactively
    if len(sys.argv) == 1:
        name = input("Group Name: ") or None
        loc = input("Location: ") or None
        id = input("RepeaterBook ID: ") or None
        call = input("Callsign: ") or None
        freq = input("Frequency (MHz): ") or None
        offset = input("Offset (MHz): ") or None
        tone = input("Tone (Hz): ") or None
        mode = input("Mode [FM]: ") or "FM"
        lat = input("Latitude: ") or None
        lon = input("Longitude: ") or None
        long_name = input("Long Name: ") or None
        url = input("Website: ") or None

        args = {
            "name": name,
            "loc": loc,
            "id": id,
            "call": call,
            "freq": freq,
            "offset": offset,
            "tone": tone,
            "mode": mode,
            "lat": lat,
            "lon": lon,
            "long_name": long_name,
            "url": url,
            "regen": False,
        }

        return SimpleNamespace(**args)

    # Otherwise, parse the arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, default=None)
    parser.add_argument("--loc", type=str, default=None)
    parser.add_argument("--id", type=str, default=None)
    parser.add_argument("--call", type=str, default=None)
    parser.add_argument("--freq", type=str, default=None)
    parser.add_argument("--offset", type=str, default=None)
    parser.add_argument("--tone", type=str, default=None)
    parser.add_argument("--mode", type=str, default="FM")
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lon", type=float, default=None)
    parser.add_argument("--long_name", type=str, default=None)
    parser.add_argument("--url", type=str, default=None)
    parser.add_argument("--regen", action="store_true")
    return parser.parse_args()


def repeater_from_repeaterbook(id_code: str) -> dict:
    """
    Extract a bunch of repeater information from RepeaterBook.

    Parameters
    ----------
    id_code : str
        The ID code from RepeaterBook.

    Returns
    -------
    Dict
        A dict containing repeat information.
    """

    # If no ID is provided, don't get any info from RepeaterBook
    if id_code is None:
        return {}

    # Otherwise, grab repeater info
    url = f"https://www.repeaterbook.com/repeaters/details.php?state_id=53&ID={id_code}"
    source = requests.get(url)

    # Extract various pieces of information
    call = source.text.split("msResult.php?call=")[1].split("&")[0]

    freq = source.text.split("Downlink:</td>\n<td>")[1].split("</td>")[0]
    freq = f"{float(freq):.04f}"
    if freq[-1] == "0":
        freq = freq[:-1]

    offset = source.text.split("Offset:</td>\n<td>\n")[1].split(" MHz")[0]
    offset = f"{float(offset):.01f}"
    if offset[0] != "-":
        offset = f"+{offset}"

    latlong = source.text.split("center: ")[1].split("\n")[0][:-1]

    try:
        tone = source.text.split("Uplink Tone:</td>\n<td>")[1].split("</td")[0]
    except IndexError:
        tone = ""

    # Try cleaning up lat / long into a Python list
    try:
        latlong = eval(latlong)
    except SyntaxError:
        pass

    repeater = {
        "Callsign": call,
        "Output (MHz)": freq,
        "Offset (MHz)": offset,
        "Tone (Hz)": tone,
        "Coordinates": latlong,
    }

    return repeater


def repeater_from_args(args: Union[argparse.Namespace, SimpleNamespace]) -> dict:
    """
    Clean up user input and return a dictionary of repeater information.

    Parameters
    ----------
    args : Union[argparse.Namespace, SimpleNamespace]
        Arguments from the command line or user input.

    Returns
    -------
    Dict
        A dict containing repeat information.
    """

    repeater = {
        "Group Name": args.name,
        "Callsign": args.call,
        "Location": args.loc,
        "Mode": args.mode,
        "Output (MHz)": args.freq,
        "Offset (MHz)": args.offset,
        "Tone (Hz)": args.tone,
        "Coordinates": [args.lat, args.lon] if args.lat and args.lon else None,
        "Long Name": args.long_name,
        "Website": args.url,
    }

    # Remove any empty values
    repeater = {key: val for key, val in repeater.items() if val}
    return repeater


def generate_repeater_df(
    args: Union[argparse.Namespace, SimpleNamespace]
) -> pd.DataFrame:
    """
    Create a DataFrame of repeaters from known repeaters combined with user input.

    Parameters
    ----------
    args : Union[argparse.Namespace, SimpleNamespace]
        User input from the command line or interactive input.

    Returns
    -------
    pd.DataFrame
        A dataframe of known repeaters from assets/repeaters.json
        combined with a new repeater taken from user input.
    """

    if args.regen:
        return pd.read_json("assets/repeaters.json", dtype=False)

    # Combine RepeaterBook info with user input
    repeaterbook = repeater_from_repeaterbook(args.id)
    repeaterargs = repeater_from_args(args)
    repeater = pd.DataFrame.from_records([{**repeaterbook, **repeaterargs}])

    # Combine with known repeaters
    df = pd.read_json("assets/repeaters.json", dtype=False)
    df = pd.concat([df, repeater], ignore_index=True)

    # Save a new known repeaters file
    df = df.reset_index(drop=True)
    df.to_json("assets/repeaters.json", orient="records", indent=4)

    return df


def remove_df_footnotes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return dataframe with Mode column footnotes removed.
    """

    return df.assign(Mode=df["Mode"].str.replace(r"\[.+\]", "", regex=True))


def format_df_for_chirp(df: pd.DataFrame) -> pd.DataFrame:
    """
    Format a DataFrame of repeaters for use in CHIRP.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.

    Returns
    -------
    pd.DataFrame
        The FM repeaters, formatted for CHIRP.
    """

    total_repeaters = len(df)

    # Only format FM channels; we can't handle DMR or D-Star at the moment
    df = df.loc[df["Mode"].isin(["FM", "NBFM", "Fusion"])].copy()  # only FM repeaters
    df.loc[df["Mode"] == "NBFM", "Mode"] = "NFM"  # NBFM -> NFM for Chirp
    df.loc[df["Mode"] == "Fusion", "Mode"] = "FM"  # Fusion -> FM for Chirp

    print(f"Chirp : {len(df)} of {total_repeaters} compatible repeaters.")

    # Set the offset direction and value
    df = df.assign(Duplex=df["Offset (MHz)"].str[0])  # + or -, first char of Offset
    df = df.assign(
        Offset=df["Offset (MHz)"].str[1:].apply(lambda x: f"{float(x):.06f}")
    )

    # Some columns can be reused
    df["Comment"] = df["Callsign"] + " - " + df["Output (MHz)"]
    df = df.rename(
        columns={
            "Callsign": "Name",
            "Output (MHz)": "Frequency",
            "Tone (Hz)": "rToneFreq",
        }
    )

    # Set some constant basics that are required for Chirp to read the file
    df["Tone"] = "Tone"
    df["cToneFreq"] = "88.5"
    df["DtcsCode"] = "023"
    df["DtcsPolarity"] = "NN"
    df["TStep"] = "5.00"

    # DCS tones are different, so go back and fix that
    dcs = df["rToneFreq"].str.startswith("D")
    df.loc[dcs, "Tone"] = "DTCS"
    df.loc[dcs, "DtcsCode"] = df.loc[dcs, "rToneFreq"].str[1:4]
    df.loc[dcs, "rToneFreq"] = "88.5"

    # The following columns are null
    for col in ["Skip", "URCALL", "RPT1CALL", "RPT2CALL", "DVCODE"]:
        df[col] = None

    # Most radios don't have a channel 0, so start the index at 1
    df.index = range(1, len(df) + 1)
    df.index.name = "Location"

    # Order columns as Chirp expects
    df = df[
        [
            "Name",
            "Frequency",
            "Duplex",
            "Offset",
            "Tone",
            "rToneFreq",
            "cToneFreq",
            "DtcsCode",
            "DtcsPolarity",
            "Mode",
            "TStep",
            "Skip",
            "Comment",
            "URCALL",
            "RPT1CALL",
            "RPT2CALL",
            "DVCODE",
        ]
    ]

    return df


def format_df_for_d878(df: pd.DataFrame) -> pd.DataFrame:
    """
    Format a DataFrame of repeaters for use in Anytone D878.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.

    Returns
    -------
    pd.DataFrame
        The FM and NBFM repeaters, and DMR repeaters in the 2m and 70cm bands,
        formatted for Anytone D878.
    """

    total_repeaters = len(df)

    # Select FM, DMR and Fusion (in FM compat mode) channels.
    df = df.loc[df["Mode"].isin(["FM", "NBFM", "DMR", "Fusion"])].copy()
    numeric_columns = ["Output (MHz)", "Offset (MHz)"]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric)
    df_2m = df.loc[(df["Output (MHz)"] > 144.0) & (df["Output (MHz)"] < 148.0)]
    df_70cm = df.loc[(df["Output (MHz)"] > 430.0) & (df["Output (MHz)"] < 450.0)]

    # Number channels from 1 ...
    df = pd.concat([df_2m, df_70cm])
    df.index = list(range(1, len(df) + 1))

    print(f"AnyTone D878 : {len(df)} of {total_repeaters} compatible repeaters.")

    df_878 = pd.DataFrame()

    df_878.index.name = "No."
    df_878["Channel Name"] = df["Callsign"]
    df_878["Receive Frequency"] = df["Output (MHz)"]
    df_878["Transmit Frequency"] = df["Output (MHz)"] + df["Offset (MHz)"]

    df_878 = df_878.round(3)

    is_dmr = df["Mode"] == "DMR"
    df_878.loc[is_dmr, "Channel Type"] = "D-Digital"
    df_878.loc[~is_dmr, "Channel Type"] = "A-Analog"
    # Not sure why this seemingly redundant column is in the format?
    df_878.loc[is_dmr, "DMR MODE"] = "1"
    df_878.loc[~is_dmr, "DMR MODE"] = "0"

    # Both DMR and NBFM are "narrow"
    is_widefm = df["Mode"].isin(["FM", "Fusion"])
    df_878.loc[is_widefm, "Band Width"] = "25K"
    df_878.loc[~is_widefm, "Band Width"] = "12.5K"

    is_dcs = df["Tone (Hz)"].str.startswith("D")
    # TODO: Use regexp for DCS tone number between D and '[' in string?
    df_878.loc[is_dcs, "CTCSS/DCS Encode"] = "D" + df["Tone (Hz)"].str[1:4] + "N"
    df_878.loc[~is_dcs, "CTCSS/DCS Encode"] = df["Tone (Hz)"]
    df_878.loc[is_dmr, "CTCSS/DCS Encode"] = None

    # Parse Tone string with DMR attributes: e.g., "CC2/TS1 BEARS1 TG/312488"
    dmr_codes = df.loc[is_dmr, "Tone (Hz)"].str.extract(
        r"CC(?P<color>\d+)\/TS(?P<slot>[12]) (?P<contact>\S+) TG\/(?P<id>\d+)"
    )
    df_878.loc[is_dmr, "Contact"] = dmr_codes["contact"]
    df_878.loc[is_dmr, "Contact TG/DMR ID"] = dmr_codes["id"]
    # Bug in CPS software - fails if Color Code column is empty - even for analog channels!
    df_878["Color Code"] = 1
    df_878.loc[is_dmr, "Color Code"] = dmr_codes["color"]
    df_878.loc[is_dmr, "Slot"] = dmr_codes["slot"]

    # TODO: Need to programatically generate a "scan list" csv file in order to be able
    # to scan all the frequencies in the Roundabout.  For now - a dummy file added to
    # ./assets.
    df_878["Scan List"] = "Roundabout"

    return df_878


def write_index_md(df: pd.DataFrame) -> None:
    """
    Write the index.md file.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.
    """

    now = datetime.now().strftime("%A %B %d at %H:%M")

    with open("assets/templates/index.md", "r") as f:
        index = f.read()

    # Fill in the number of repeaters and the updated date
    index = index.replace("{{ n_repeaters }}", str(len(df)))
    index = index.replace("{{ date_updated }}", now)
    index = index.replace("{{ n_groups }}", str(df["Group Name"].nunique()))

    with open("index.md", "w") as f:
        f.write(index)


def write_repeaters_md(df: pd.DataFrame) -> None:
    """
    Write the repeaters.md file.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.
    """

    # Create the main markdown table
    table_cols = [
        "Group Name",
        "Callsign",
        "Location",
        "Mode",
        "Output (MHz)",
        "Offset (MHz)",
        "Tone (Hz)",
    ]
    table = df[table_cols].to_markdown(
        index=False,
        disable_numparse=True,
        colalign=["left", "left", "left", "left", "right", "right", "right"],
    )

    # Create a list of short-name-to-long-description mappings
    association_text = ""
    associations = df.groupby("Group Name")[["Long Name", "Website"]].first()
    for short, long, url in associations.to_records():
        association_text += f"{short}\n: [{long}]({url})\n\n"

    # Write the markdown file from template
    with open("assets/templates/repeaters.md", "r") as f:
        maps = f.read()
    maps = maps.replace("{{ table }}", table)
    maps = maps.replace("{{ associations }}", association_text)
    with open("repeaters.md", "w") as f:
        f.write(maps)


def write_map_md(df: pd.DataFrame, threshold: float = 0.03) -> None:
    """
    Write the map.md file.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.
    threshold : float, optional
        Repeaters are combined into a single pin if less than this distance apart, by default 0.03.
    """

    coords = np.array(df["Coordinates"].to_list())
    dist = pdist(coords)
    allocations = fcluster(
        linkage(dist, method="complete"), threshold, criterion="distance"
    )

    clusters = defaultdict(list)
    for idx, allocation in enumerate(allocations):
        clusters[allocation].append(
            df.iloc[idx][["Callsign", "Output (MHz)", "Coordinates"]].to_dict()
        )

    pins = []

    for cluster in clusters.values():

        msg = ""
        for repeater in cluster:
            msg += f"{repeater['Callsign']} {repeater['Output (MHz)']}<br>"

        if len(msg):
            coords = np.mean([repeater["Coordinates"] for repeater in cluster], axis=0)
            coords = f"[{coords[0]:.10f}, {coords[1]:.10f}]"
            pins.append(f"L.marker({coords}).bindPopup('{msg}').addTo(map);")

    pins = "\n".join(pins)

    with open("assets/templates/map.md", "r") as f:
        maps_md = f.read()
    with open("assets/templates/map.html", "r") as f:
        maps_html = f.read()

    maps_md = maps_md.replace("{{ repeater_pins }}", pins)
    maps_html = maps_html.replace("{{ repeater_pins }}", pins)

    with open("map.md", "w") as f:
        f.write(maps_md)
    with open("demo_map.html", "w") as f:
        f.write(maps_html)


def write_chirp_csv(df: pd.DataFrame) -> None:
    """
    Write the chirp.csv file.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.
    """

    df = format_df_for_chirp(df)
    df.to_csv("assets/rr_frequencies.csv")


def write_d878_zip(df: pd.DataFrame) -> None:
    """
    Write the Anytone D878 csv file.

    Parameters
    ----------
    df : pd.DataFrame
        All of the repeaters.
    """

    df = format_df_for_d878(df)
    df.to_csv("assets/d878.csv")
    # TODO : scanlist and talk groups files here

    with ZipFile("assets/d878.zip", "w") as zipf:
        zipf.write("assets/d878.csv", arcname="d878.csv")
        zipf.write("assets/d878-scanlist.csv", arcname="d878-scanlist.csv")
        zipf.write("assets/d878-talk-groups.csv", arcname="d878-talk-groups.csv")


if __name__ == "__main__":

    args = parse_args()

    df = generate_repeater_df(args)

    write_index_md(df)
    write_repeaters_md(df)
    write_map_md(df)

    df = remove_df_footnotes(df)
    write_chirp_csv(df)
    write_d878_zip(df)
