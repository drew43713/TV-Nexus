import os
import sqlite3
import html
import gzip
import xml.etree.ElementTree as ET
from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE


import os
import sqlite3
import html
import gzip
import xml.etree.ElementTree as ET
from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, HOST_IP, PORT

def parse_epg_files():
    print("Parsing EPG files...")
    """
    Parse all raw EPG files from EPG_DIR (files ending in .xml, .xmltv, or .gz)
    and merge them into a combined file (EPG.xml) in MODIFIED_EPG_DIR.
    
    The process builds the channel mapping using:
      - the tvg_name field (used for matching) and, as a fallback,
      - the channel's display name (i.e. the channel name from the database).
    
    When creating the <channel> element in the output, it uses the channel's 
    name and logo from the database (preserving the original display data), 
    while programme elements get updated with the correct channel attribute.
    """
    # Gather all raw EPG files.
    epg_files = [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.lower().endswith((".xml", ".xmltv", ".gz"))
    ]
    if not epg_files:
        print("[INFO] No EPG files found.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Clear existing EPG program data.
    c.execute("DELETE FROM epg_programs")

    # Build mappings from the database.
    # Mapping for matching: use both tvg_name and channel name (as fallback).
    # Also build a channel info mapping (channel id -> (name, logo_url))
    c.execute("SELECT id, tvg_name, name, logo_url FROM channels")
    tvgname_to_dbid = {}
    channel_info = {}
    for row in c.fetchall():
        db_id = row[0]
        db_tvg_name = row[1]
        db_name = row[2]
        db_logo = row[3]
        channel_info[str(db_id)] = (db_name, db_logo)
        if db_tvg_name:
            tvgname_to_dbid[html.unescape(db_tvg_name)] = db_id
        # Also add the channel name as a fallback key.
        if db_name:
            tvgname_to_dbid[html.unescape(db_name)] = db_id

    # Create a new combined XML tree with a <tv> root.
    combined_root = ET.Element("tv")
    combined_channels = {}  # To avoid duplicate <channel> elements.
    # For programme elements: mapping from raw channel id to the database channel id (as string)
    file_channel_mapping = {}

    # Process each raw EPG file.
    for epg_file in epg_files:
        print(f"[INFO] Parsing EPG: {epg_file}")
        try:
            with open(epg_file, "rb") as f:
                magic = f.read(2)
                f.seek(0)
                if magic == b'\x1f\x8b':
                    tree = ET.parse(gzip.open(f))
                else:
                    tree = ET.parse(f)
            root = tree.getroot()

            # Process each <channel> element.
            for channel_el in list(root.findall("channel")):
                raw_channel_id = channel_el.get("id", "").strip()
                unescaped_raw_id = html.unescape(raw_channel_id)
                # Get the raw display name from the file.
                display_name_el = channel_el.find("display-name")
                raw_display = (
                    html.unescape(display_name_el.text.strip())
                    if display_name_el is not None and display_name_el.text
                    else ""
                )
                new_id = None
                # First, try matching by the raw channel id (assuming it was originally set as tvg_name).
                if unescaped_raw_id in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[unescaped_raw_id]
                # Otherwise, try matching using the display name.
                elif raw_display in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[raw_display]

                if not new_id:
                    # Skip this channel if no match is found.
                    continue

                new_id_str = str(new_id)
                # Record mapping for programme elements.
                file_channel_mapping[raw_channel_id] = new_id_str

                # Add the <channel> element to the combined XML only once.
                if new_id_str not in combined_channels:
                    new_channel_el = ET.Element("channel", id=new_id_str)
                    # Use database info to set the display name and logo.
                    if new_id_str in channel_info:
                        ch_name, ch_logo = channel_info[new_id_str]
                        if ch_logo:
                            # If the logo URL is relative (starts with "/"), convert it to a full URL.
                            if ch_logo.startswith("/"):
                                # Try to get BASE_URL from the environment.
                                base_url = os.environ.get("BASE_URL", "").strip()
                                if base_url:
                                    base_url = base_url.rstrip("/")
                                else:
                                    # Fallback to config values.
                                    base_url = f"http://{HOST_IP}:{PORT}"
                                    #print(f"[DEBUG] Fallback base_url from config: {base_url}")
                                full_logo_url = f"{base_url}{ch_logo}"
                            else:
                                full_logo_url = ch_logo
                            icon_el = ET.Element("icon", src=full_logo_url)
                            new_channel_el.append(icon_el)
                        if ch_name:
                            disp_el = ET.Element("display-name")
                            disp_el.text = ch_name  # Always use the original channel name.
                            new_channel_el.append(disp_el)
                    combined_root.append(new_channel_el)
                    combined_channels[new_id_str] = new_channel_el

            # Process each <programme> element.
            for prog_el in list(root.findall("programme")):
                raw_prog_channel = prog_el.get("channel", "").strip()
                if raw_prog_channel not in file_channel_mapping:
                    # Skip programmes whose channel did not match.
                    continue
                new_prog_channel = file_channel_mapping[raw_prog_channel]
                prog_el.set("channel", new_prog_channel)

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
                """, (new_prog_channel, start_time, stop_time, title_text, desc_text))
                # Append the programme element to the combined XML.
                combined_root.append(prog_el)

        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")

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

def update_program_data_for_channel(channel_id: int):
    """
    Update the programme data in the modified EPG file for a given channel.
    This function:
      1. Reads the new tvg_name from the database for the specified channel.
      2. Opens the modified EPG file and removes all <programme> elements for that channel.
      3. Deletes any programme records in the database for that channel.
      4. Iterates over the raw EPG files and, using matching logic based on the new tvg_name,
         finds and appends programme elements for that channel.
      5. Saves the modified EPG file.
    """
    # Get the new tvg_name for this channel from the database.
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tvg_name FROM channels WHERE id = ?", (channel_id,))
    row = c.fetchone()
    if not row:
        print(f"[ERROR] Channel {channel_id} not found in database.")
        conn.close()
        return
    new_tvg_name = row[0]
    conn.close()

    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    
    # Load the modified EPG file.
    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
    except Exception as e:
        print(f"[ERROR] Unable to load modified EPG file: {e}")
        return

    # Remove all programme elements for this channel.
    for prog in list(root.findall("programme")):
        if prog.get("channel") == str(channel_id):
            root.remove(prog)

    # Delete programme data for this channel in the database.
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs WHERE channel_tvg_name = ?", (str(channel_id),))
    conn.commit()
    conn.close()

    # Process raw EPG files to re-add programme data for this channel.
    epg_files = [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.lower().endswith((".xml", ".xmltv", ".gz"))
    ]
    if not epg_files:
        print("[INFO] No raw EPG files found.")
        return

    # For each raw EPG file…
    for epg_file in epg_files:
        print(f"[INFO] Re-processing raw EPG file: {epg_file} for channel {channel_id}")
        try:
            with open(epg_file, "rb") as f:
                magic = f.read(2)
                f.seek(0)
                if magic == b'\x1f\x8b':
                    tree_raw = ET.parse(gzip.open(f))
                else:
                    tree_raw = ET.parse(f)
            root_raw = tree_raw.getroot()

            # In this file, look at each <channel> element to see if it should map to our channel.
            # We use the same matching logic as in parse_epg_files(), but only care about our channel.
            file_channel_mapping = {}
            for channel_el in list(root_raw.findall("channel")):
                raw_channel_id = channel_el.get("id", "").strip()
                unescaped_raw_id = html.unescape(raw_channel_id)
                display_name_el = channel_el.find("display-name")
                raw_display = (
                    html.unescape(display_name_el.text.strip())
                    if display_name_el is not None and display_name_el.text
                    else ""
                )
                # Here, we consider the channel a match if either the raw id or display text equals the new tvg_name.
                if unescaped_raw_id == new_tvg_name or raw_display == new_tvg_name:
                    # Map this raw channel to our database channel.
                    file_channel_mapping[raw_channel_id] = str(channel_id)

            # Now, for each <programme> element in the raw file…
            for prog_el in list(root_raw.findall("programme")):
                raw_prog_channel = prog_el.get("channel", "").strip()
                if raw_prog_channel not in file_channel_mapping:
                    continue  # This programme does not belong to our channel.
                # Update its channel attribute.
                new_prog_channel = file_channel_mapping[raw_prog_channel]
                prog_el.set("channel", new_prog_channel)

                # Insert programme details into the database.
                start_time = prog_el.get("start", "").strip()
                stop_time = prog_el.get("stop", "").strip()
                title_el = prog_el.find("title")
                desc_el = prog_el.find("desc")
                title_text = title_el.text.strip() if (title_el is not None and title_el.text) else ""
                desc_text = desc_el.text.strip() if (desc_el is not None and desc_el.text) else ""
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("""
                    INSERT INTO epg_programs
                    (channel_tvg_name, start, stop, title, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (new_prog_channel, start_time, stop_time, title_text, desc_text))
                conn.commit()
                conn.close()

                # Append this programme element to the modified EPG file.
                root.append(prog_el)
        except Exception as e:
            print(f"[ERROR] Processing file {epg_file} failed: {e}")

    # Save the updated modified EPG file.
    tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
    print(f"[SUCCESS] Updated programme data for channel {channel_id} in modified EPG file.")