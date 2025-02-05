import os
import sqlite3
import xml.etree.ElementTree as ET
from .config import EPG_DIR, MODIFIED_EPG_DIR, DB_FILE

def parse_epg_files():
    epg_files = [
        os.path.join(EPG_DIR, f)
        for f in os.listdir(EPG_DIR)
        if f.endswith(".xml") or f.endswith(".xmltv")
    ]
    if not epg_files:
        print("[INFO] No EPG files found.")
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs")
    c.execute("SELECT tvg_name, id FROM channels")
    rows = c.fetchall()
    tvgname_to_dbid = {row[0]: row[1] for row in rows if row[0]}
    c.execute("SELECT tvg_name, logo_url FROM channels")
    logo_map = dict(c.fetchall())

    for epg_file in epg_files:
        print(f"[INFO] Parsing EPG: {epg_file}")
        try:
            tree = ET.parse(epg_file)
            root = tree.getroot()
            oldid_to_newid = {}
            for channel_el in list(root.findall("channel")):
                old_epg_id = channel_el.get("id", "").strip()
                display_name_el = channel_el.find("display-name")
                display_name = display_name_el.text.strip() if (display_name_el is not None and display_name_el.text) else ""
                new_id = None
                if old_epg_id in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[old_epg_id]
                elif display_name in tvgname_to_dbid:
                    new_id = tvgname_to_dbid[display_name]
                if not new_id:
                    root.remove(channel_el)
                    continue
                channel_el.set("id", str(new_id))
                oldid_to_newid[old_epg_id] = str(new_id)
                if display_name in logo_map and logo_map[display_name]:
                    icon_el = channel_el.find("icon")
                    if icon_el is None:
                        icon_el = ET.SubElement(channel_el, "icon")
                    icon_el.set("src", logo_map[display_name])
            for prog_el in list(root.findall("programme")):
                prog_channel = prog_el.get("channel", "").strip()
                if prog_channel not in oldid_to_newid:
                    root.remove(prog_el)
                    continue
                new_prog_channel_id = oldid_to_newid[prog_channel]
                prog_el.set("channel", new_prog_channel_id)
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
            modified_epg_file = os.path.join(MODIFIED_EPG_DIR, os.path.basename(epg_file))
            tree.write(modified_epg_file, encoding="utf-8", xml_declaration=True)
            print(f"[SUCCESS] Filtered EPG saved as {modified_epg_file}")
        except Exception as e:
            print(f"[ERROR] Parsing {epg_file} failed: {e}")
    conn.commit()
    conn.close()

def update_modified_epg(old_id: int, new_id: int, swap: bool):
    epg_files = [os.path.join(MODIFIED_EPG_DIR, f) for f in os.listdir(MODIFIED_EPG_DIR)
                 if f.endswith(".xml") or f.endswith(".xmltv")]
    if not epg_files:
        return
    epg_file = epg_files[0]
    try:
        tree = ET.parse(epg_file)
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
        tree.write(epg_file, encoding="utf-8", xml_declaration=True)
    except Exception as e:
        print("Error updating modified EPG file:", e)
