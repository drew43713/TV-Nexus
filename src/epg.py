import os
import gzip
import html
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import re
import json
import random
from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, BASE_URL, EPG_COLORS_FILE

import os
import gzip
import html
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import re
import json
import random
from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, BASE_URL, EPG_COLORS_FILE

# ====================================================
# 1) Helper to parse/normalize XMLTV date/time
# ====================================================
def parse_xmltv_datetime(dt_str):
    match = re.match(r'^(\d{14})(?:\s*([+-]\d{4}))?$', dt_str.replace('-', ' -').replace('+', ' +'))
    if not match:
        return "19700101000000 +0000"
    dt_main, offset_str = match.groups()
    try:
        naive_dt = datetime.strptime(dt_main, "%Y%m%d%H%M%S")
    except ValueError:
        return "19700101000000 +0000"
    if offset_str:
        sign = 1 if offset_str.startswith('+') else -1
        hours = int(offset_str[1:3])
        minutes = int(offset_str[3:5])
        offset_delta = sign * (hours * 60 + minutes)
        utc_dt = naive_dt - timedelta(minutes=offset_delta)
    else:
        utc_dt = naive_dt
    return utc_dt.strftime("%Y%m%d%H%M%S") + " +0000"

# ====================================================
# 2) Parse raw EPG files into 'raw_epg_*' tables
# ====================================================
def parse_raw_epg_files():
    print("[INFO] Parsing raw EPG files...")
    epg_files = [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.lower().endswith((".xml", ".xmltv", ".gz"))
    ]
    if not epg_files:
        print("[INFO] No EPG files found in EPG_DIR.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Clear previous raw EPG data.
    c.execute("DELETE FROM raw_epg_channels")
    c.execute("DELETE FROM raw_epg_programs")

    # (Table creation is now handled in database.py.)

    for epg_file in epg_files:
        print(f"[INFO] Reading raw EPG: {epg_file}")
        try:
            with open(epg_file, "rb") as f:
                magic = f.read(2)
                f.seek(0)
                if magic == b'\x1f\x8b':
                    tree = ET.parse(gzip.open(f))
                else:
                    tree = ET.parse(f)
            root = tree.getroot()
            # Process channel elements.
            for channel_el in root.findall("channel"):
                raw_id = html.unescape(channel_el.get("id", "").strip())
                # Create a composite key: raw_id + "::" + (filename)
                composite_raw_id = f"{raw_id}::{os.path.basename(epg_file)}"
                display_names = channel_el.findall("display-name")
                if len(display_names) >= 2:
                    abbreviation = html.unescape(display_names[0].text.strip()) if display_names[0].text else ""
                    full_name = html.unescape(display_names[1].text.strip()) if display_names[1].text else ""
                    disp_name = f"{full_name} ({abbreviation})" if full_name and abbreviation else full_name or abbreviation
                elif len(display_names) == 1:
                    disp_name = html.unescape(display_names[0].text.strip())
                else:
                    disp_name = ""
                # Use INSERT OR IGNORE to avoid duplicate (composite_raw_id, file) entries.
                c.execute(
                    "INSERT OR IGNORE INTO raw_epg_channels (raw_id, display_name, raw_epg_file) VALUES (?, ?, ?)",
                    (composite_raw_id, disp_name, os.path.basename(epg_file))
                )

            # Process programme elements.
            for prog_el in root.findall("programme"):
                raw_prog_channel = html.unescape(prog_el.get("channel", "").strip())
                # Create the composite key for programmes too.
                composite_prog_channel = f"{raw_prog_channel}::{os.path.basename(epg_file)}"
                raw_start_time = prog_el.get("start", "").strip()
                raw_stop_time = prog_el.get("stop", "").strip()
                start_time = parse_xmltv_datetime(raw_start_time)
                stop_time = parse_xmltv_datetime(raw_stop_time)
                title_el = prog_el.find("title")
                title_text = title_el.text.strip() if (title_el is not None and title_el.text) else ""
                desc_el = prog_el.find("desc")
                desc_text = desc_el.text.strip() if (desc_el is not None and desc_el.text) else ""
                icon_el = prog_el.find("icon")
                icon_src = icon_el.get("src", "").strip() if icon_el is not None else ""
                c.execute("""
                    INSERT INTO raw_epg_programs (raw_channel_id, start, stop, title, description, icon_url, raw_epg_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (composite_prog_channel, start_time, stop_time, title_text, desc_text, icon_src, os.path.basename(epg_file)))
        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")

    conn.commit()
    conn.close()
    print("[INFO] Finished populating raw_epg_* tables.")
    
# ====================================================
# 3) Build combined EPG from raw data (full rebuild)
# ====================================================
def build_combined_epg():
    print("[INFO] Building combined EPG from raw DB...")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Remove old programme entries.
    c.execute("DELETE FROM epg_programs")
    combined_root = ET.Element("tv")

    # Query active channels using channel_number for ordering.
    c.execute("""
        SELECT channel_number, tvg_name, name, logo_url 
          FROM channels 
         WHERE active = 1 
      ORDER BY channel_number
    """)
    db_channels = c.fetchall()

    for (channel_number, db_tvg_name, db_name, db_logo) in db_channels:
        db_tvg_name = db_tvg_name or ""
        db_name = db_name or ""
        db_logo = db_logo or ""

        # Build the <channel> element using channel_number as the ID.
        channel_el = ET.Element("channel", id=str(channel_number))
        disp_el = ET.Element("display-name")
        disp_el.text = db_name
        channel_el.append(disp_el)
        if db_logo:
            if db_logo.startswith("/"):
                full_logo_url = f"{BASE_URL}{db_logo}"
            else:
                full_logo_url = db_logo
            icon_el = ET.Element("icon", src=full_logo_url)
            channel_el.append(icon_el)
        combined_root.append(channel_el)

        # Insert epg_channels row if not present
        c.execute("INSERT OR IGNORE INTO epg_channels (name) VALUES (?)", (db_name,))

        # Find matching raw channel IDs.
        raw_ids = []
        if db_tvg_name:
            c.execute("""
                SELECT DISTINCT raw_id 
                  FROM raw_epg_channels 
                 WHERE raw_id = ? OR display_name = ?
            """, (db_tvg_name, db_tvg_name))
            raw_ids = [r[0] for r in c.fetchall()]
            if not raw_ids:
                c.execute("""
                    SELECT DISTINCT raw_id
                      FROM raw_epg_channels
                     WHERE display_name = ?
                """, (db_name,))
                raw_ids = [r[0] for r in c.fetchall()]
        else:
            c.execute("""
                SELECT DISTINCT raw_id
                  FROM raw_epg_channels
                 WHERE display_name = ?
            """, (db_name,))
            raw_ids = [r[0] for r in c.fetchall()]

        # For each matching raw channel, pull programme data
        for rid in raw_ids:
            c.execute("""
                SELECT start, stop, title, description, icon_url
                  FROM raw_epg_programs 
                 WHERE raw_channel_id = ?
            """, (rid,))
            raw_progs = c.fetchall()
            for (start_t, stop_t, title_txt, desc_txt, icon_url) in raw_progs:
                c.execute("""
                    INSERT INTO epg_programs (channel_tvg_name, start, stop, title, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (str(channel_number), start_t, stop_t, title_txt, desc_txt))
                prog_el = ET.Element("programme", {
                    "channel": str(channel_number),
                    "start": start_t,
                    "stop": stop_t
                })
                t_el = ET.SubElement(prog_el, "title")
                t_el.text = title_txt
                d_el = ET.SubElement(prog_el, "desc")
                d_el.text = desc_txt
                if icon_url:
                    filename = os.path.basename(icon_url)
                    new_icon_url = f"{BASE_URL}/schedulesdirect_cache/{filename}"
                    icon_el = ET.Element("icon", {"src": new_icon_url})
                    prog_el.append(icon_el)
                combined_root.append(prog_el)

    conn.commit()
    conn.close()
    os.makedirs(MODIFIED_EPG_DIR, exist_ok=True)
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    tree = ET.ElementTree(combined_root)
    tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
    print(f"[SUCCESS] Combined EPG saved as {combined_epg_file}")

# ====================================================
# 4) Update program data for a single channel
# ====================================================
def update_program_data_for_channel(db_id: int):
    """
    CHANGED:
      - We now fetch channel_number from the channel with the given DB ID.
      - Then we remove old EPG data using that channel_number, 
        and re-insert partial EPG for that channel_number.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT channel_number, tvg_name, name, logo_url FROM channels WHERE id = ?", (db_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        print(f"[ERROR] Channel ID {db_id} not found.")
        return
    channel_number, db_tvg_name, db_name, db_logo = row
    conn.close()

    # Remove old programs from DB and EPG.xml using channel_number, 
    # because epg_programs.channel_tvg_name = str(channel_number).
    _remove_programs_from_db(channel_number)
    _remove_programs_from_xml(channel_number)

    # Reload the partial EPG for that channel_number from raw_epg_* 
    # (matching tvg_name or name).
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    db_tvg_name = db_tvg_name or ""
    db_name = db_name or ""

    # Find raw IDs in raw_epg_channels
    raw_ids = []
    if db_tvg_name:
        c.execute("""
            SELECT DISTINCT raw_id
              FROM raw_epg_channels
             WHERE raw_id = ? OR display_name = ?
        """, (db_tvg_name, db_tvg_name))
        raw_ids = [r[0] for r in c.fetchall()]
        if not raw_ids:
            c.execute("""
                SELECT DISTINCT raw_id
                  FROM raw_epg_channels
                 WHERE display_name = ?
            """, (db_name,))
            raw_ids = [r[0] for r in c.fetchall()]
    else:
        c.execute("""
            SELECT DISTINCT raw_id
              FROM raw_epg_channels
             WHERE display_name = ?
        """, (db_name,))
        raw_ids = [r[0] for r in c.fetchall()]

    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        print("[WARN] EPG.xml not found; build_combined_epg may be needed first.")
        conn.close()
        return

    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
    except Exception as e:
        print(f"[ERROR] Unable to load {combined_epg_file}: {e}")
        conn.close()
        return

    # Ensure <channel> node is present or create it
    channel_el = None
    for ch_el in root.findall("channel"):
        if ch_el.get("id") == str(channel_number):
            channel_el = ch_el
            break
    if channel_el is None:
        channel_el = ET.Element("channel", {"id": str(channel_number)})
        disp_el = ET.SubElement(channel_el, "display-name")
        disp_el.text = db_name
        if db_logo:
            if db_logo.startswith("/"):
                full_logo_url = f"{BASE_URL}{db_logo}"
            else:
                full_logo_url = db_logo
            ET.SubElement(channel_el, "icon", {"src": full_logo_url})
        root.append(channel_el)

    # Insert new program data
    for rid in raw_ids:
        c.execute("""
            SELECT start, stop, title, description, icon_url
              FROM raw_epg_programs
             WHERE raw_channel_id = ?
        """, (rid,))
        raw_progs = c.fetchall()
        for (start_t, stop_t, title_txt, desc_txt, icon_url) in raw_progs:
            # epg_programs DB
            c.execute("""
                INSERT INTO epg_programs (channel_tvg_name, start, stop, title, description)
                VALUES (?, ?, ?, ?, ?)
            """, (str(channel_number), start_t, stop_t, title_txt, desc_txt))

            # EPG.xml node
            prog_el = ET.Element("programme", {
                "channel": str(channel_number),
                "start": start_t,
                "stop": stop_t
            })
            t_el = ET.SubElement(prog_el, "title")
            t_el.text = title_txt
            d_el = ET.SubElement(prog_el, "desc")
            d_el.text = desc_txt
            if icon_url:
                filename = os.path.basename(icon_url)
                new_icon_url = f"{BASE_URL}/schedulesdirect_cache/{filename}"
                icon_el = ET.Element("icon", {"src": new_icon_url})
                prog_el.append(icon_el)
            root.append(prog_el)

    conn.commit()
    conn.close()

    tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
    print(f"[INFO] Updated partial EPG for channel_number {channel_number} in {combined_epg_file}")

def _remove_programs_from_db(channel_number: int):
    """
    CHANGED: This now accepts channel_number, because epg_programs.channel_tvg_name 
    is stored as the channel_number in string form.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs WHERE channel_tvg_name = ?", (str(channel_number),))
    conn.commit()
    conn.close()

def _remove_programs_from_xml(channel_number: int):
    """
    CHANGED: Also keyed by channel_number in <programme channel="...">
    """
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return
    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        removed_count = 0
        for prog_el in list(root.findall("programme")):
            if prog_el.get("channel") == str(channel_number):
                root.remove(prog_el)
                removed_count += 1
        if removed_count > 0:
            tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
            print(f"[INFO] Removed {removed_count} old programmes for channel_number {channel_number}.")
    except Exception as e:
        print(f"[ERROR] Could not remove old programmes from EPG.xml: {e}")

def update_channel_logo_in_epg(channel_id: int, new_logo: str):
    """
    Unchanged logic, but keep in mind this uses channel_id or channel_number?
    Actually, it calls <channel id="channel_number"> if you are consistent 
    in your build_combined_epg. So if you want to update the EPG channel node's icon, 
    we must do the same approach as above:
    1) fetch channel_number from the channel with the given DB ID
    2) update <channel id="channel_number">
    """
    # CHANGED: fetch channel_number from DB
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT channel_number FROM channels WHERE id = ?", (channel_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        print(f"[ERROR] update_channel_logo_in_epg: no channel found with id={channel_id}")
        return
    channel_number = row[0]

    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return
    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        updated = False
        for channel_el in root.findall("channel"):
            if channel_el.get("id") == str(channel_number):
                if new_logo.startswith("/"):
                    full_logo_url = f"{BASE_URL}{new_logo}"
                else:
                    full_logo_url = new_logo
                icon_el = channel_el.find("icon")
                if icon_el is not None:
                    icon_el.set("src", full_logo_url)
                else:
                    icon_el = ET.Element("icon", src=full_logo_url)
                    channel_el.append(icon_el)
                updated = True
                break
        if updated:
            tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
            print(f"[INFO] Updated channel_number {channel_number} logo in EPG.xml.")
    except Exception as e:
        print(f"[ERROR] update_channel_logo_in_epg: {e}")

def update_channel_metadata_in_epg(channel_id: int, new_name: str, new_logo: str):
    """
    Similar approach: fetch channel_number from the DB, update <channel id=channel_number>.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT channel_number FROM channels WHERE id = ?", (channel_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        print(f"[ERROR] Could not update channel {channel_id} metadata in EPG.xml: channel not found.")
        return
    channel_number = row[0]

    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return
    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        for ch_el in root.findall("channel"):
            if ch_el.get("id") == str(channel_number):
                disp_el = ch_el.find("display-name")
                if disp_el is not None:
                    disp_el.text = new_name
                else:
                    disp_el = ET.Element("display-name")
                    disp_el.text = new_name
                    ch_el.append(disp_el)
                if new_logo.startswith("/"):
                    full_logo_url = f"{BASE_URL}{new_logo}"
                else:
                    full_logo_url = new_logo
                icon_el = ch_el.find("icon")
                if icon_el is not None:
                    icon_el.set("src", full_logo_url)
                else:
                    icon_el = ET.Element("icon", src=full_logo_url)
                    ch_el.append(icon_el)
    except Exception as e:
        print(f"[ERROR] Could not update channel_number {channel_number} metadata in EPG.xml: {e}")

def update_modified_epg(old_id: int, new_id: int, swap: bool):
    """
    Here old_id and new_id refer to the channel_number, not DB IDs.
    We'll also fix the DB epg_programs table references in the same call.
    """
    # CHANGED: also fix references in the DB
    update_programs_db_on_swap(old_id, new_id, swap)

    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return
    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        if swap:
            for ch in root.findall("channel"):
                cid = ch.get("id")
                if cid == str(old_id):
                    ch.set("id", str(new_id))
                elif cid == str(new_id):
                    ch.set("id", str(old_id))
            for prog in root.findall("programme"):
                pcid = prog.get("channel")
                if pcid == str(old_id):
                    prog.set("channel", str(new_id))
                elif pcid == str(new_id):
                    prog.set("channel", str(old_id))
        else:
            for ch in root.findall("channel"):
                if ch.get("id") == str(old_id):
                    ch.set("id", str(new_id))
            for prog in root.findall("programme"):
                if prog.get("channel") == str(old_id):
                    prog.set("channel", str(new_id))

        tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
        print(f"[INFO] update_modified_epg: changed channel {old_id} -> {new_id} (swap={swap})")
    except Exception as e:
        print(f"[ERROR] update_modified_epg: {e}")


def update_programs_db_on_swap(old_num: int, new_num: int, do_swap: bool):
    """
    Fix references in the epg_programs table, 
    where channel_tvg_name is a string of the channel_number.
    If do_swap == True, we do a 3-step swap using a temp value.
    Otherwise, it's just a direct rename from old_num to new_num.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if do_swap:
        # pick a temp channel name that won't conflict
        temp_str = f"swaptemp_{random.randint(1,999999)}"
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (temp_str, str(old_num)))
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(old_num), str(new_num)))
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(new_num), temp_str))
    else:
        c.execute("UPDATE epg_programs SET channel_tvg_name = ? WHERE channel_tvg_name = ?", (str(new_num), str(old_num)))

    conn.commit()
    conn.close()
    print(f"[INFO] epg_programs references updated from {old_num} to {new_num}, swap={do_swap}.")
    
    
# ====================================================
# EPG Colors functionality remains unchanged
# ====================================================
def get_color_for_epg_file(filename):
    mapping = load_epg_color_mapping()
    if filename in mapping:
        return mapping[filename]
    color = "#%06x" % random.randint(0, 0xFFFFFF)
    mapping[filename] = color
    save_epg_color_mapping(mapping)
    return color

def load_epg_color_mapping():
    directory = os.path.dirname(EPG_COLORS_FILE)
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    if not os.path.exists(EPG_COLORS_FILE):
        with open(EPG_COLORS_FILE, "w") as f:
            json.dump({}, f)
        return {}
    try:
        with open(EPG_COLORS_FILE, "r") as f:
            mapping = json.load(f)
    except Exception:
        mapping = {}
    return mapping

def save_epg_color_mapping(mapping):
    with open(EPG_COLORS_FILE, "w") as f:
        json.dump(mapping, f, indent=4)

