import os
import sqlite3
import html
import gzip
import xml.etree.ElementTree as ET
from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE

def parse_epg_files():
    """
    Parse all EPG files from EPG_DIR (which may be .xml, .xmltv, or .gz)
    and merge them into a single combined file (EPG.xml) in MODIFIED_EPG_DIR.
    Only channels that match the database (using tvg_name) are kept.
    """
    # Gather all EPG files.
    epg_files = [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.lower().endswith((".xml", ".xmltv", ".gz"))
    ]
    if not epg_files:
        print("[INFO] No EPG files found.")
        return

    # Open a database connection.
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Clear existing EPG program data.
    c.execute("DELETE FROM epg_programs")

    # Build a mapping from unescaped tvg_name to channel database ID.
    c.execute("SELECT tvg_name, id FROM channels")
    tvgname_to_dbid = {html.unescape(row[0]): row[1] for row in c.fetchall() if row[0]}

    # Build a logo mapping from unescaped tvg_name to logo URL.
    c.execute("SELECT tvg_name, logo_url FROM channels")
    logo_map = {html.unescape(row[0]): row[1] for row in c.fetchall()}

    # Create a new combined XML tree with a <tv> root.
    combined_root = ET.Element("tv")

    # To avoid duplicate channels in the combined file, keep track of channels already added.
    # key: new channel id (as a string)
    combined_channels = {}

    # Process each EPG file.
    for epg_file in epg_files:
        print(f"[INFO] Parsing EPG: {epg_file}")
        try:
            # Open file with proper handling for gzipped files.
            with open(epg_file, "rb") as f:
                # Check first two bytes to decide if file is gzipped.
                magic = f.read(2)
                f.seek(0)
                if magic == b'\x1f\x8b':
                    tree = ET.parse(gzip.open(f))
                else:
                    tree = ET.parse(f)

            root = tree.getroot()

            # Build a mapping for channels in this file:
            # key: original channel id (from the file)
            # value: new channel id (from DB mapping)
            file_channel_mapping = {}

            # Process each <channel> element.
            for channel_el in list(root.findall("channel")):
                # Get the channel's original id.
                old_epg_id = channel_el.get("id", "").strip()
                unescaped_old_epg_id = html.unescape(old_epg_id)

                # Retrieve the display-name (if present).
                display_name_el = channel_el.find("display-name")
                display_name = (
                    html.unescape(display_name_el.text.strip())
                    if display_name_el is not None and display_name_el.text
                    else ""
                )

                new_id = None
                # Try matching by the original id (after unescaping) first.
                if unescaped_old_epg_id in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[unescaped_old_epg_id]
                elif display_name in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[display_name]

                if not new_id:
                    # Skip this channel if no matching DB channel is found.
                    continue

                # Use the new id for the channel.
                channel_el.set("id", str(new_id))
                file_channel_mapping[old_epg_id] = str(new_id)

                # If this channel is not already added to the combined file, add it.
                if str(new_id) not in combined_channels:
                    # If there's a matching logo, update or add the <icon> element.
                    if display_name in logo_map and logo_map[display_name]:
                        icon_el = channel_el.find("icon")
                        if icon_el is None:
                            icon_el = ET.Element("icon")
                            channel_el.append(icon_el)
                        icon_el.set("src", logo_map[display_name])
                    combined_root.append(channel_el)
                    combined_channels[str(new_id)] = channel_el

            # Process each <programme> element.
            for prog_el in list(root.findall("programme")):
                prog_channel = prog_el.get("channel", "").strip()
                if prog_channel not in file_channel_mapping:
                    # Skip programme if its channel did not match.
                    continue
                # Update programme's channel attribute.
                new_prog_channel_id = file_channel_mapping[prog_channel]
                prog_el.set("channel", new_prog_channel_id)

                # Insert programme details into the database.
                start_time = prog_el.get("start", "").strip()
                stop_time = prog_el.get("stop", "").strip()
                title_el = prog_el.find("title")
                desc_el = prog_el.find("desc")
                title_text = title_el.text.strip() if (title_el is not None and title_el.text) else ""
                desc_text = desc_el.text.strip() if (desc_el is not None and desc_el.text) else ""

                c.execute("""
                    INSERT INTO epg_programs
                    (channel_tvg_name, start, stop, title, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (new_prog_channel_id, start_time, stop_time, title_text, desc_text))

                # Append the programme element to the combined XML.
                combined_root.append(prog_el)

        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")

    # Commit database changes.
    conn.commit()
    conn.close()

    # Save the combined XML to a single file named EPG.xml.
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    tree = ET.ElementTree(combined_root)
    tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
    print(f"[SUCCESS] Combined EPG saved as {combined_epg_file}")


def update_modified_epg(old_id: int, new_id: int, swap: bool):
    """
    Update the combined modified EPG file (EPG.xml) by swapping or updating channel IDs.
    """
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        if swap:
            for ch in root.findall("channel"):
                id_val = ch.get("id")
                if id_val == str(old_id):
                    ch.set("id", str(new_id))
                elif id_val == str(new_id):
                    ch.set("id", str(old_id))
            for prog in root.findall("programme"):
                chan = prog.get("channel")
                if chan == str(old_id):
                    prog.set("channel", str(new_id))
                elif chan == str(new_id):
                    prog.set("channel", str(old_id))
        else:
            for ch in root.findall("channel"):
                if ch.get("id") == str(old_id):
                    ch.set("id", str(new_id))
            for prog in root.findall("programme"):
                if prog.get("channel") == str(old_id):
                    prog.set("channel", str(new_id))
        tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
    except Exception as e:
        print("Error updating modified EPG file:", e)
