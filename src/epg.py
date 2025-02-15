import os
import gzip
import html
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import re

from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE, BASE_URL

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
# 2) Parse raw EPG files into 'raw_epg_*' tables (including icon)
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
    
    # Create raw_epg_channels table.
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_epg_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id TEXT,
            display_name TEXT
        )
    ''')
    
    # Create raw_epg_programs table with an extra icon_url column.
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_epg_programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_channel_id TEXT,
            start TEXT,
            stop TEXT,
            title TEXT,
            description TEXT,
            icon_url TEXT
        )
    ''')
    
    # Ensure the icon_url column exists (in case the table existed before).
    c.execute("PRAGMA table_info(raw_epg_programs)")
    columns = [col_info[1] for col_info in c.fetchall()]
    if "icon_url" not in columns:
        try:
            c.execute("ALTER TABLE raw_epg_programs ADD COLUMN icon_url TEXT")
        except Exception as e:
            print(f"[WARNING] Could not add icon_url column: {e}")
    
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
                display_names = channel_el.findall("display-name")
                if len(display_names) >= 2:
                    abbreviation = html.unescape(display_names[0].text.strip()) if display_names[0].text else ""
                    full_name = html.unescape(display_names[1].text.strip()) if display_names[1].text else ""
                    disp_name = f"{full_name} ({abbreviation})" if full_name and abbreviation else full_name or abbreviation
                elif len(display_names) == 1:
                    disp_name = html.unescape(display_names[0].text.strip())
                else:
                    disp_name = ""
                c.execute("INSERT INTO raw_epg_channels (raw_id, display_name) VALUES (?, ?)", (raw_id, disp_name))
            
            # Process programme elements.
            for prog_el in root.findall("programme"):
                raw_prog_channel = html.unescape(prog_el.get("channel", "").strip())
                raw_start_time = prog_el.get("start", "").strip()
                raw_stop_time = prog_el.get("stop", "").strip()
                start_time = parse_xmltv_datetime(raw_start_time)
                stop_time = parse_xmltv_datetime(raw_stop_time)
                title_el = prog_el.find("title")
                title_text = title_el.text.strip() if (title_el is not None and title_el.text) else ""
                desc_el = prog_el.find("desc")
                desc_text = desc_el.text.strip() if (desc_el is not None and desc_el.text) else ""
                # Parse optional icon element.
                icon_el = prog_el.find("icon")
                icon_src = ""
                if icon_el is not None:
                    icon_src = icon_el.get("src", "").strip()
                c.execute("""
                    INSERT INTO raw_epg_programs (raw_channel_id, start, stop, title, description, icon_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (raw_prog_channel, start_time, stop_time, title_text, desc_text, icon_src))
        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")

    conn.commit()
    conn.close()
    print("[INFO] Finished populating raw_epg_* tables.")

# ====================================================
# 3) Build combined EPG from raw data (full rebuild, including icon rewriting)
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

        # Build the channel element.
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

        # Optionally, save channel name in epg_channels table.
        c.execute("INSERT OR IGNORE INTO epg_channels (name) VALUES (?)", (db_name,))

        # Find matching raw channel IDs.
        raw_ids = []
        if db_tvg_name:
            c.execute(
                "SELECT DISTINCT raw_id FROM raw_epg_channels WHERE raw_id = ? OR display_name = ?",
                (db_tvg_name, db_tvg_name)
            )
            raw_ids = [r[0] for r in c.fetchall()]
        else:
            c.execute(
                "SELECT DISTINCT raw_id FROM raw_epg_channels WHERE display_name = ?",
                (db_name,)
            )
            raw_ids = [r[0] for r in c.fetchall()]

        # For each matching raw channel, pull programme data.
        for rid in raw_ids:
            c.execute("""
                SELECT start, stop, title, description, icon_url
                  FROM raw_epg_programs 
                 WHERE raw_channel_id = ?
            """, (rid,))
            raw_progs = c.fetchall()
            for (start_t, stop_t, title_txt, desc_txt, icon_url) in raw_progs:
                # Insert into the epg_programs table.
                c.execute("""
                    INSERT INTO epg_programs (channel_tvg_name, start, stop, title, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (str(channel_number), start_t, stop_t, title_txt, desc_txt))
                # Build the programme element.
                prog_el = ET.Element("programme", {
                    "channel": str(channel_number),
                    "start": start_t,
                    "stop": stop_t
                })
                t_el = ET.SubElement(prog_el, "title")
                t_el.text = title_txt
                d_el = ET.SubElement(prog_el, "desc")
                d_el.text = desc_txt
                # If an icon was provided, rewrite its URL.
                if icon_url:
                    filename = os.path.basename(icon_url)
                    # Build new URL: using schedulesdirect_cache folder.
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
# (No big file re-parse)
# ====================================================
def update_program_data_for_channel(channel_id: int):
    """
    1) Fetch the channel's tvg_name (and name) from 'channels'.
    2) Remove old programme data from epg_programs & EPG.xml for that channel.
    3) Find matching raw EPG data from 'raw_epg_*' tables for that tvg_name or name.
    4) Insert it into epg_programs & EPG.xml (only for this channel).
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tvg_name, name, logo_url FROM channels WHERE id = ?", (channel_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        print(f"[ERROR] Channel {channel_id} not found.")
        return
    db_tvg_name, db_name, db_logo = row
    conn.close()

    db_tvg_name = db_tvg_name or ""
    db_name = db_name or ""

    _remove_programs_from_db(channel_id)
    _remove_programs_from_xml(channel_id)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    raw_ids = []
    if db_tvg_name:
        c.execute("""
            SELECT DISTINCT raw_id
              FROM raw_epg_channels
             WHERE raw_id = ?
                OR display_name = ?
        """, (db_tvg_name, db_tvg_name))
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
        print("[WARN] EPG.xml not found; you may need to build_combined_epg first.")
        conn.close()
        return

    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
    except Exception as e:
        print(f"[ERROR] Unable to load {combined_epg_file}: {e}")
        conn.close()
        return

    # Ensure <channel> node is present.
    channel_el = None
    for ch_el in root.findall("channel"):
        if ch_el.get("id") == str(channel_id):
            channel_el = ch_el
            break
    if channel_el is None:
        channel_el = ET.Element("channel", {"id": str(channel_id)})
        disp_el = ET.SubElement(channel_el, "display-name")
        disp_el.text = db_name
        if db_logo:
            if db_logo.startswith("/"):
                full_logo_url = f"{BASE_URL}{db_logo}"
            else:
                full_logo_url = db_logo
            ET.SubElement(channel_el, "icon", {"src": full_logo_url})
        root.append(channel_el)

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
            """, (str(channel_id), start_t, stop_t, title_txt, desc_txt))

            prog_el = ET.Element("programme", {
                "channel": str(channel_id),
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
    print(f"[INFO] Updated EPG for channel {channel_id} in {combined_epg_file}")

def _remove_programs_from_db(channel_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs WHERE channel_tvg_name = ?", (str(channel_id),))
    conn.commit()
    conn.close()

def _remove_programs_from_xml(channel_id: int):
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return

    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        removed_count = 0
        for prog_el in list(root.findall("programme")):
            if prog_el.get("channel") == str(channel_id):
                root.remove(prog_el)
                removed_count += 1
        if removed_count > 0:
            tree.write(combined_epg_file, encoding="utf-8", xml_declaration=True)
            print(f"[INFO] Removed {removed_count} old programmes for channel {channel_id}.")
    except Exception as e:
        print(f"[ERROR] Could not remove old programmes from EPG.xml: {e}")

# ====================================================
# 5) Update channel logos or metadata
# ====================================================
def update_channel_logo_in_epg(channel_id: int, new_logo: str):
    """
    Update just the <icon> for this channel in EPG.xml.
    Does not re-parse raw EPG.
    """
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return

    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()
        updated = False

        for channel_el in root.findall("channel"):
            if channel_el.get("id") == str(channel_id):
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
            print(f"[INFO] Updated channel {channel_id} logo in EPG.xml.")
    except Exception as e:
        print(f"[ERROR] update_channel_logo_in_epg: {e}")

def update_channel_metadata_in_epg(channel_id: int, new_name: str, new_logo: str):
    """
    Update the <display-name> and <icon> for a channel in EPG.xml.
    """
    combined_epg_file = os.path.join(MODIFIED_EPG_DIR, "EPG.xml")
    if not os.path.exists(combined_epg_file):
        return

    try:
        tree = ET.parse(combined_epg_file)
        root = tree.getroot()

        for ch_el in root.findall("channel"):
            if ch_el.get("id") == str(channel_id):
                # Update display-name.
                disp_el = ch_el.find("display-name")
                if disp_el is not None:
                    disp_el.text = new_name
                else:
                    disp_el = ET.Element("display-name")
                    disp_el.text = new_name
                    ch_el.append(disp_el)

                # Update icon.
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
        print(f"[ERROR] Could not update channel {channel_id} metadata in EPG.xml: {e}")

def update_modified_epg(old_id: int, new_id: int, swap: bool):
    """
    If you rename channel IDs, also update them in EPG.xml <channel> and <programme>.
    This does NOT parse raw EPG or do partial merges—just edits the final EPG.xml.
    """
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
