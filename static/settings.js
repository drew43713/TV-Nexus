let epgParsingInProgress = false;
let epgUploadingInProgress = false;
let epgDeletingInProgress = false;
let m3uUploadingInProgress = false;

window.addEventListener("DOMContentLoaded", () => {
  // Inject compact styles for the stream status table/actions
  if (!document.getElementById("stream-status-styles")) {
    const style = document.createElement("style");
    style.id = "stream-status-styles";
    style.textContent = `
      .stream-status-table { width: 100%; border-collapse: collapse; }
      .stream-status-table th, .stream-status-table td { border-bottom: 1px solid var(--bs-border-color, rgba(0,0,0,0.15)); padding: 6px 8px; text-align: left; vertical-align: top; }
      .stream-status-table tr:hover { background: var(--bs-table-hover-bg, rgba(0,0,0,0.03)); }
      .stream-status-table td:last-child { white-space: nowrap; }
      .stream-status-table a { word-break: break-all; }
      .stream-status-table .stop-stream-btn { 
        display: inline-flex; align-items: center; justify-content: center; gap: 6px;
        padding: 4px 8px; font-size: 12px; line-height: 1.2; border-radius: var(--bs-border-radius, .375rem);
        border: 1px solid var(--bs-danger, #dc3545); background: transparent; color: var(--bs-danger, #dc3545);
        cursor: pointer; transition: background-color .15s ease, color .15s ease; 
      }
      .stream-status-table .stop-stream-btn:hover { background: color-mix(in srgb, var(--bs-danger, #dc3545) 10%, transparent); }
      .stream-status-table .stop-stream-btn[disabled] { opacity: .6; cursor: not-allowed; }
      @media (prefers-color-scheme: dark) {
        .stream-status-table th, .stream-status-table td { border-bottom-color: var(--bs-border-color, #2b2b2b); }
        .stream-status-table tr:hover { background: var(--bs-table-hover-bg, rgba(255,255,255,0.06)); }
      }
    `;
    document.head.appendChild(style);
  }

  const urlParams = new URLSearchParams(window.location.search);
  if (
    urlParams.has("updated") ||
    urlParams.has("epg_upload_success") ||
    urlParams.has("m3u_upload_success") ||
    urlParams.has("parse_epg_success")
  ) {
    window.history.replaceState({}, document.title, "/settings");
  }

  updateStreamStatus();
  setInterval(updateStreamStatus, 5000);

  document.querySelectorAll(".config-form form").forEach((form) => {
    if (!form.id) {
      form.addEventListener("submit", () => {
        setTimeout(() => {
          location.reload();
        }, 3000);
      });
    }
  });

  const uploadForm = document.getElementById("upload-epg-form");
  if (uploadForm) {
    uploadForm.addEventListener("submit", uploadEPG);
  }

  const uploadM3UForm = document.getElementById("upload-m3u-form");
  if (uploadM3UForm) {
    uploadM3UForm.addEventListener("submit", uploadM3U);
  }

  document.querySelectorAll(".delete-epg-form").forEach((form) => {
    form.addEventListener("submit", deleteEPG);
  });

  document.querySelectorAll(".epg-color-dot").forEach((dot) => {
    dot.addEventListener("click", function () {
      const picker = this.parentElement.querySelector(".epg-color-picker");
      if (picker) {
        picker.click();
      }
    });
  });

  document.querySelectorAll(".epg-color-picker").forEach((picker) => {
    picker.addEventListener("change", updateEpgColor);
  });

  // Initialize FFmpeg Profiles UI (modal)
  initFfmpegProfilesUI();
});

function updateStreamStatus() {
  if (epgParsingInProgress || epgUploadingInProgress || epgDeletingInProgress || m3uUploadingInProgress)
    return;

  fetch("/api/stream_status")
    .then((response) => response.json())
    .then((data) => {
      const container = document.getElementById("stream-status-content");
      if (Object.keys(data).length === 0) {
        container.innerHTML = "<p>No active streams.</p>";
      } else {
        let html = "<table class=\"stream-status-table\"><thead><tr>";
        html += "<th>Channel Number</th>";
        html += "<th>Channel Name</th>";
        html += "<th>Current Program</th>";
        html += "<th>Subscribers</th>";
        html += "<th>Stream URL</th>";
        html += "<th>Video Codec</th>";
        html += "<th>Resolution</th>";
        html += "<th>Audio Info</th>";
        html += "<th>Actions</th>";

        html += "</tr></thead><tbody>";

        for (const channel in data) {
          const stream = data[channel];
          const subscribers = stream.subscriber_count;
          const streamUrl = stream.stream_url;
          const channelName = stream.channel_name || "N/A";

          let currentProgram = "N/A";
          if (stream.current_program && stream.current_program.title) {
            currentProgram = stream.current_program.title;
          }

          let videoCodec = "N/A";
          let resolution = "N/A";
          let audioInfo = "N/A";

          if (stream.probe_info && stream.probe_info.streams) {
            const streams = stream.probe_info.streams;
            const videoStream = streams.find((s) => s.codec_type === "video");
            if (videoStream) {
              videoCodec = videoStream.codec_name;
              resolution = videoStream.width + "x" + videoStream.height;
            }
            const audioStream = streams.find((s) => s.codec_type === "audio");
            if (audioStream) {
              audioInfo =
                audioStream.codec_name +
                " " +
                audioStream.sample_rate +
                "Hz " +
                audioStream.channels +
                "ch";
            }
          }

          html += `<tr>
                    <td>${channel}</td>
                    <td>${channelName}</td>
                    <td>${currentProgram}</td>
                    <td>${subscribers}</td>
                    <td><a href="${streamUrl}" target="_blank">${streamUrl}</a></td>
                    <td>${videoCodec}</td>
                    <td>${resolution}</td>
                    <td>${audioInfo}</td>
                    <td><button class="stop-stream-btn" data-channel="${channel}" title="Stop this stream" aria-label="Stop stream for channel ${channel}">Stop</button></td>
                  </tr>`;
        }

        html += "</tbody></table>";
        container.innerHTML = html;

        // Wire Stop buttons
        container.querySelectorAll('.stop-stream-btn').forEach((btn) => {
          btn.addEventListener('click', onStopStreamClick);
        });
      }
    })
    .catch((error) => {
      document.getElementById("stream-status-content").innerHTML =
        "<p>Error fetching stream status: " + error + "</p>";
    });
}

async function stopStream(btn) {
  if (!btn) return;
  const channel = btn.getAttribute('data-channel');
  if (!channel) return;

  if (!confirm(`Stop stream for channel ${channel}?`)) return;

  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Stopping...';

  try {
    const res = await fetch('/api/streams/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel })
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    // Attempt to parse JSON, but tolerate plain text
    let payload = null;
    try { payload = await res.json(); } catch (_) {}

    // Refresh the stream status to reflect the stopped stream
    updateStreamStatus();
  } catch (err) {
    alert('Failed to stop stream: ' + err);
    btn.disabled = false;
    btn.textContent = originalText;
    return;
  }

  // Give a short delay before re-enabling in case the row persists
  setTimeout(() => {
    if (document.body.contains(btn)) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }, 1500);
}

function onStopStreamClick(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  stopStream(btn);
}

function parseEPG() {
  const parseButton = document.getElementById("parse-epg-button");
  parseButton.disabled = true;
  epgParsingInProgress = true;

  const statusEl = document.getElementById("parse-epg-status");
  statusEl.innerText = "Parsing EPG files... please wait.";
  statusEl.style.display = "block";

  fetch("/parse_epg", { method: "POST" })
    .then((response) => {
      if (!response.ok) {
        throw new Error("Failed to parse EPG files.");
      }
      return response.json();
    })
    .then((data) => {
      if (data.success) {
        statusEl.innerText = data.message;
      } else {
        statusEl.innerText = "Error: " + data.message;
      }
      epgParsingInProgress = false;
      setTimeout(() => {
        statusEl.style.display = "none";
        location.reload();
      }, 3000);
    })
    .catch((error) => {
      statusEl.innerText = "Error parsing EPG files: " + error.message;
      epgParsingInProgress = false;
      parseButton.disabled = false;
    });
}

function uploadEPG(event) {
  event.preventDefault();
  epgUploadingInProgress = true;

  const form = document.getElementById("upload-epg-form");
  const formData = new FormData(form);
  const statusEl = document.getElementById("upload-epg-status");
  statusEl.innerText = "Uploading EPG file... please wait.";
  statusEl.style.display = "block";

  fetch("/upload_epg", {
    method: "POST",
    body: formData,
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error("Upload failed");
      }
      return response.json();
    })
    .then((data) => {
      if (data.success) {
        statusEl.innerText = data.message;
      } else {
        statusEl.innerText = "Error: " + data.message;
      }
      setTimeout(() => {
        statusEl.style.display = "none";
        epgUploadingInProgress = false;
        location.reload();
      }, 3000);
    })
    .catch((error) => {
      statusEl.innerText = "Error uploading EPG: " + error.message;
      setTimeout(() => {
        statusEl.style.display = "none";
        epgUploadingInProgress = false;
      }, 5000);
    });
}

function uploadM3U(event) {
  event.preventDefault();
  m3uUploadingInProgress = true;

  const form = document.getElementById("upload-m3u-form");
  const formData = new FormData(form);
  const statusEl = document.getElementById("upload-m3u-status");
  statusEl.innerText = "Uploading M3U file... please wait.";
  statusEl.style.display = "block";

  fetch("/upload_m3u", {
    method: "POST",
    body: formData,
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error("Upload failed");
      }
      return response.json();
    })
    .then((data) => {
      if (data.success) {
        statusEl.innerText = data.message;
      } else {
        statusEl.innerText = "Error: " + data.message;
      }
      setTimeout(() => {
        statusEl.style.display = "none";
        m3uUploadingInProgress = false;
        location.reload();
      }, 3000);
    })
    .catch((error) => {
      statusEl.innerText = "Error uploading M3U: " + error.message;
      setTimeout(() => {
        statusEl.style.display = "none";
        m3uUploadingInProgress = false;
      }, 5000);
    });
}

function deleteEPG(event) {
  event.preventDefault();
  epgDeletingInProgress = true;

  const form = event.target;
  const formData = new FormData(form);
  const statusEl = document.getElementById("delete-epg-status");
  statusEl.innerText = "Deleting EPG file... please wait.";
  statusEl.style.display = "block";

  fetch("/delete_epg", {
    method: "POST",
    body: formData,
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error("Delete failed");
      }
      return response.json();
    })
    .then((data) => {
      if (data.success) {
        statusEl.innerText = data.message;
      } else {
        statusEl.innerText = "Error: " + data.message;
      }
      setTimeout(() => {
        statusEl.style.display = "none";
        epgDeletingInProgress = false;
        location.reload();
      }, 3000);
    })
    .catch((error) => {
      statusEl.innerText = "Error deleting EPG file: " + error.message;
      setTimeout(() => {
        statusEl.style.display = "none";
        epgDeletingInProgress = false;
      }, 5000);
    });
}

function updateEpgColor(event) {
  const picker = event.target;
  const filename = picker.getAttribute("data-filename");
  const newColor = picker.value;
  fetch("/update_epg_color", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body:
      "filename=" +
      encodeURIComponent(filename) +
      "&color=" +
      encodeURIComponent(newColor),
  })
    .then((response) => response.json())
    .then((data) => {
      if (data.success) {
        const dot = picker.parentElement.querySelector(".epg-color-dot");
        if (dot) {
          dot.style.background = newColor;
        }
      } else {
        alert("Error updating color: " + data.message);
      }
    })
    .catch((error) => {
      alert("Error updating color: " + error.message);
    });
}

// ---------------- FFmpeg Profiles Modal UI ----------------
function initFfmpegProfilesUI() {
  // Inject styles once
  if (!document.getElementById("ffmpeg-profiles-styles")) {
    const style = document.createElement("style");
    style.id = "ffmpeg-profiles-styles";
    style.textContent = `
      /* Base styles use inheritance so they follow the page theme */
      .ffmpeg-fab { position: fixed; right: 16px; bottom: 16px; z-index: 1000; background: var(--bs-primary, #0d6efd); color: var(--bs-btn-color, #fff); border: 1px solid transparent; border-radius: var(--bs-border-radius-pill, 50px); padding: 10px 14px; cursor: pointer; box-shadow: 0 2px 6px rgba(0,0,0,0.2); font: inherit; }
      .ffmpeg-fab:hover { background: var(--bs-primary-hover, #0b5ed7); }
      .ffmpeg-modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: none; z-index: 1000; }
      .ffmpeg-modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: inherit; color: inherit; border: 1px solid; border-color: var(--bs-border-color, rgba(0,0,0,0.15)); border-radius: var(--bs-border-radius-lg, .5rem); width: min(800px, 92vw); max-height: 80vh; overflow: auto; display: none; z-index: 1001; box-shadow: 0 6px 20px rgba(0,0,0,0.25); }
      .ffmpeg-modal header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid; border-bottom-color: var(--bs-border-color, rgba(0,0,0,0.15)); }
      .ffmpeg-modal h2 { margin: 0; font-size: 1.1rem; }
      .ffmpeg-modal .close { background: transparent; border: none; font-size: 1.25rem; cursor: pointer; color: inherit; }
      .ffmpeg-modal .content { padding: 12px 16px; }
      .ffmpeg-profiles-list table { width: 100%; border-collapse: collapse; color: inherit; }
      .ffmpeg-profiles-list th, .ffmpeg-profiles-list td { border-bottom: 1px solid; border-bottom-color: var(--bs-border-color, rgba(0,0,0,0.15)); padding: 8px; text-align: left; vertical-align: top; }
      .ffmpeg-profiles-list tr:hover { background: var(--bs-table-hover-bg, rgba(0,0,0,0.03)); }
      .ffmpeg-badge { display: inline-block; padding: 2px 6px; border-radius: 10px; font-size: 12px; background: var(--bs-secondary-bg, #e9ecef); color: var(--bs-secondary-color, #495057); margin-left: 8px; }
      .ffmpeg-actions button, #ffmpeg-add-profile-btn { background: var(--bs-btn-bg, transparent); color: inherit; border: 1px solid; border-color: var(--bs-border-color, rgba(0,0,0,0.15)); border-radius: var(--bs-border-radius, .375rem); padding: 6px 10px; font: inherit; cursor: pointer; }
      .ffmpeg-actions button:hover, #ffmpeg-add-profile-btn:hover { background: var(--bs-secondary-bg, rgba(0,0,0,0.04)); }
      .ffmpeg-actions button[disabled] { opacity: .6; cursor: not-allowed; }
      .ffmpeg-form { display: grid; grid-template-columns: 140px 1fr; gap: 8px 12px; align-items: center; margin-top: 12px; }
      .ffmpeg-form label { font-weight: 600; }
      .ffmpeg-form input, .ffmpeg-form textarea { width: 100%; padding: 6px 8px; background: inherit; color: inherit; border: 1px solid; border-color: var(--bs-border-color, rgba(0,0,0,0.15)); border-radius: var(--bs-border-radius, .375rem); font: inherit; }
      .ffmpeg-form .hint { grid-column: 1 / -1; font-size: 12px; color: var(--bs-secondary-color, #6c757d); }
      .ffmpeg-error { color: var(--bs-danger, #dc3545); margin-top: 8px; }
      .ffmpeg-success { color: var(--bs-success, #198754); margin-top: 8px; }
      code { color: inherit; }

      /* System dark mode adjustments when the page doesn't expose variables */
      @media (prefers-color-scheme: dark) {
        :root:not([data-theme="light"]) .ffmpeg-modal { background: var(--bs-body-bg, #1e1e1e); color: var(--bs-body-color, #e9ecef); border-color: var(--bs-border-color, #2b2b2b); }
        :root:not([data-theme="light"]) .ffmpeg-modal header { border-bottom-color: var(--bs-border-color, #2b2b2b); }
        :root:not([data-theme="light"]) .ffmpeg-profiles-list th, :root:not([data-theme="light"]) .ffmpeg-profiles-list td { border-bottom-color: var(--bs-border-color, #2b2b2b); }
        :root:not([data-theme="light"]) .ffmpeg-profiles-list tr:hover { background: var(--bs-table-hover-bg, rgba(255,255,255,0.06)); }
        :root:not([data-theme="light"]) .ffmpeg-badge { background: var(--bs-secondary-bg, #2a2a2a); color: var(--bs-secondary-color, #cfd3d7); }
        :root:not([data-theme="light"]) .ffmpeg-actions button, :root:not([data-theme="light"]) #ffmpeg-add-profile-btn { border-color: var(--bs-border-color, #3a3a3a); }
        :root:not([data-theme="light"]) .ffmpeg-actions button:hover, :root:not([data-theme="light"]) #ffmpeg-add-profile-btn:hover { background: var(--bs-secondary-bg, #2a2a2a); }
        :root:not([data-theme="light"]) .ffmpeg-form input, :root:not([data-theme="light"]) .ffmpeg-form textarea { border-color: var(--bs-border-color, #3a3a3a); background: var(--bs-body-bg, #1e1e1e); color: var(--bs-body-color, #e9ecef); }
        :root:not([data-theme="light"]) .ffmpeg-form .hint { color: var(--bs-secondary-color, #9aa0a6); }
      }
    `;
    document.head.appendChild(style);
  }

  // Create floating action button
  if (!document.getElementById("ffmpeg-profiles-fab")) {
    const fab = document.createElement("button");
    fab.id = "ffmpeg-profiles-fab";
    fab.className = "ffmpeg-fab";
    fab.textContent = "FFmpeg Profiles";
    fab.title = "Manage FFmpeg profiles";
    fab.addEventListener("click", openFfmpegProfilesModal);
    document.body.appendChild(fab);
  }

  // Create modal/backdrop if not present
  if (!document.getElementById("ffmpeg-profiles-backdrop")) {
    const backdrop = document.createElement("div");
    backdrop.id = "ffmpeg-profiles-backdrop";
    backdrop.className = "ffmpeg-modal-backdrop";
    backdrop.addEventListener("click", closeFfmpegProfilesModal);

    const modal = document.createElement("div");
    modal.id = "ffmpeg-profiles-modal";
    modal.className = "ffmpeg-modal";

    modal.innerHTML = `
      <header>
        <h2>FFmpeg Profiles</h2>
        <button class="close" aria-label="Close">×</button>
      </header>
      <div class="content">
        <div id="ffmpeg-profiles-feedback"></div>
        <section class="ffmpeg-profiles-list">
          <h3 style="margin-top:0">Available Profiles</h3>
          <div id="ffmpeg-profiles-table"></div>
        </section>
        <section id="ffmpeg-edit-profile" class="ffmpeg-edit-profile" style="display:none; margin-top: 16px;">
          <h3 style="margin-top:0">Edit Profile</h3>
          <div class="ffmpeg-form">
            <label for="ffmpeg-edit-name">Name</label>
            <input id="ffmpeg-edit-name" type="text" readonly />
            <label for="ffmpeg-edit-args">Args</label>
            <textarea id="ffmpeg-edit-args" rows="8" style="resize: vertical;"></textarea>
            <div class="hint">Edit the full args string. Include {input} where the URL should go. Do not include the initial 'ffmpeg' token.</div>
            <div style="grid-column: 1 / -1; display: flex; gap: 8px;">
              <button id="ffmpeg-edit-save">Save Changes</button>
              <button type="button" id="ffmpeg-edit-cancel">Cancel</button>
            </div>
          </div>
          <div id="ffmpeg-edit-profile-feedback"></div>
        </section>
        <section class="ffmpeg-add-profile">
          <h3>Add Custom Profile</h3>
          <div class="ffmpeg-form">
            <label for="ffmpeg-profile-name">Name</label>
            <input id="ffmpeg-profile-name" type="text" placeholder="e.g., MyH264Profile" />
            <label for="ffmpeg-profile-args">Args</label>
            <textarea id="ffmpeg-profile-args" rows="3" placeholder="-hide_banner -loglevel error -re -i {input} -c:v libx264 -preset fast -c:a aac -f mpegts pipe:1"></textarea>
            <div class="hint">Include {input} where the URL should go. Do not include the initial 'ffmpeg' token.</div>
            <div style="grid-column: 1 / -1;">
              <button id="ffmpeg-add-profile-btn">Add Profile</button>
            </div>
          </div>
          <div id="ffmpeg-add-profile-feedback"></div>
        </section>
      </div>
    `;

    document.body.appendChild(backdrop);
    document.body.appendChild(modal);

    modal.querySelector(".close").addEventListener("click", closeFfmpegProfilesModal);
    document.getElementById("ffmpeg-add-profile-btn").addEventListener("click", onAddFfmpegProfile);

    const editSaveBtn = document.getElementById("ffmpeg-edit-save");
    const editCancelBtn = document.getElementById("ffmpeg-edit-cancel");
    if (editSaveBtn) editSaveBtn.addEventListener("click", saveEditProfile);
    if (editCancelBtn) editCancelBtn.addEventListener("click", closeEditProfile);
  }
}

function openFfmpegProfilesModal() {
  const backdrop = document.getElementById("ffmpeg-profiles-backdrop");
  const modal = document.getElementById("ffmpeg-profiles-modal");
  if (!backdrop || !modal) return;
  backdrop.style.display = "block";
  modal.style.display = "block";
  loadFfmpegProfiles();
}

function closeFfmpegProfilesModal() {
  const backdrop = document.getElementById("ffmpeg-profiles-backdrop");
  const modal = document.getElementById("ffmpeg-profiles-modal");
  if (backdrop) backdrop.style.display = "none";
  if (modal) modal.style.display = "none";
}

async function loadFfmpegProfiles() {
  const tableContainer = document.getElementById("ffmpeg-profiles-table");
  const feedback = document.getElementById("ffmpeg-profiles-feedback");
  feedback.textContent = "";
  tableContainer.innerHTML = "Loading...";

  try {
    const res = await fetch("/api/ffmpeg/profiles");
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();
    renderFfmpegProfilesTable(data);
  } catch (err) {
    tableContainer.innerHTML = "";
    feedback.innerHTML = `<div class="ffmpeg-error">Unable to load profiles (is the backend endpoint implemented?) — ${err}</div>`;
  }
}

function renderFfmpegProfilesTable(data) {
  const tableContainer = document.getElementById("ffmpeg-profiles-table");
  const profiles = (data && data.profiles) || [];
  const selected = (data && data.selected) || "CPU";

  if (!profiles.length) {
    tableContainer.innerHTML = "<p>No profiles found.</p>";
    return;
  }

  let html = "<table><thead><tr>" +
             "<th>Name</th>" +
             "<th>Args</th>" +
             "<th>Actions</th>" +
             "</tr></thead><tbody>";

  for (const p of profiles) {
    const name = p.name || "(unnamed)";
    const argsStr = (typeof p.args_str === "string") ? p.args_str : (Array.isArray(p.args) ? p.args.join(" ") : (p.args || ""));
    const isSelected = name === selected;
    html += `<tr>
      <td>${name}${isSelected ? '<span class="ffmpeg-badge">Selected</span>' : ''}</td>
      <td><code>${escapeHtml(argsStr)}</code></td>
      <td class="ffmpeg-actions">
        <button data-action="select" data-name="${encodeURIComponent(name)}" ${isSelected ? 'disabled' : ''}>Select</button>
        <button data-action="edit" data-name="${encodeURIComponent(name)}" data-args="${encodeURIComponent(argsStr)}" ${name === 'CPU' || name === 'CUDA' ? 'disabled' : ''}>Edit</button>
        <button data-action="delete" data-name="${encodeURIComponent(name)}" ${name === 'CPU' || name === 'CUDA' ? 'disabled' : ''}>Delete</button>
      </td>
    </tr>`;
  }

  html += "</tbody></table>";
  tableContainer.innerHTML = html;

  // Wire actions
  tableContainer.querySelectorAll("button[data-action]").forEach((btn) => {
    const action = btn.getAttribute("data-action");
    const rawName = btn.getAttribute("data-name");
    const name = rawName ? decodeURIComponent(rawName) : "";
    if (action === "select") btn.addEventListener("click", () => selectFfmpegProfile(name));
    if (action === "delete") btn.addEventListener("click", () => deleteFfmpegProfile(name));
    if (action === "edit") {
      btn.addEventListener("click", () => {
        const raw = btn.getAttribute("data-args") || "";
        const currentArgs = decodeURIComponent(raw);
        editFfmpegProfile(name, currentArgs);
      });
    }
  });
}

function onStopStreamClick(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  stopStream(btn);
}

async function selectFfmpegProfile(name) {
  const feedback = document.getElementById("ffmpeg-profiles-feedback");
  feedback.textContent = "";
  try {
    const res = await fetch("/api/ffmpeg/profiles/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    await loadFfmpegProfiles();
    feedback.innerHTML = `<div class="ffmpeg-success">Selected profile: <strong>${escapeHtml(name)}</strong></div>`;
  } catch (err) {
    feedback.innerHTML = `<div class="ffmpeg-error">Failed to select profile — ${err}</div>`;
  }
}

async function onAddFfmpegProfile() {
  const nameEl = document.getElementById("ffmpeg-profile-name");
  const argsEl = document.getElementById("ffmpeg-profile-args");
  const feedback = document.getElementById("ffmpeg-add-profile-feedback");
  feedback.textContent = "";

  const name = (nameEl.value || "").trim();
  const args = (argsEl.value || "").trim();
  if (!name || !args) {
    feedback.innerHTML = '<div class="ffmpeg-error">Name and args are required.</div>';
    return;
  }

  try {
    const res = await fetch("/api/ffmpeg/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, args }),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    nameEl.value = "";
    argsEl.value = "";
    await loadFfmpegProfiles();
    feedback.innerHTML = `<div class="ffmpeg-success">Added profile: <strong>${escapeHtml(name)}</strong></div>`;
  } catch (err) {
    feedback.innerHTML = `<div class="ffmpeg-error">Failed to add profile — ${err}</div>`;
  }
}

async function deleteFfmpegProfile(name) {
  if (!confirm(`Delete profile "${name}"?`)) return;
  const feedback = document.getElementById("ffmpeg-profiles-feedback");
  feedback.textContent = "";
  try {
    const res = await fetch(`/api/ffmpeg/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    await loadFfmpegProfiles();
    feedback.innerHTML = `<div class="ffmpeg-success">Deleted profile: <strong>${escapeHtml(name)}</strong></div>`;
  } catch (err) {
    feedback.innerHTML = `<div class="ffmpeg-error">Failed to delete profile — ${err}</div>`;
  }
}

async function editFfmpegProfile(name, currentArgs) {
  openEditProfile(name, currentArgs);
}

function openEditProfile(name, argsStr) {
  const section = document.getElementById("ffmpeg-edit-profile");
  const nameEl = document.getElementById("ffmpeg-edit-name");
  const argsEl = document.getElementById("ffmpeg-edit-args");
  const feedback = document.getElementById("ffmpeg-edit-profile-feedback");
  if (!section || !nameEl || !argsEl) return;
  nameEl.value = name || "";
  argsEl.value = argsStr || "";
  if (feedback) feedback.textContent = "";
  section.style.display = "block";
  argsEl.focus();
}

function closeEditProfile() {
  const section = document.getElementById("ffmpeg-edit-profile");
  const nameEl = document.getElementById("ffmpeg-edit-name");
  const argsEl = document.getElementById("ffmpeg-edit-args");
  const feedback = document.getElementById("ffmpeg-edit-profile-feedback");
  if (section) section.style.display = "none";
  if (nameEl) nameEl.value = "";
  if (argsEl) argsEl.value = "";
  if (feedback) feedback.textContent = "";
}

async function saveEditProfile(event) {
  if (event && event.preventDefault) event.preventDefault();
  const nameEl = document.getElementById("ffmpeg-edit-name");
  const argsEl = document.getElementById("ffmpeg-edit-args");
  const feedback = document.getElementById("ffmpeg-edit-profile-feedback");
  const listFeedback = document.getElementById("ffmpeg-profiles-feedback");
  const name = (nameEl && nameEl.value) || "";
  const args = (argsEl && argsEl.value) || "";
  if (!name) {
    if (feedback) feedback.innerHTML = '<div class="ffmpeg-error">Missing profile name.</div>';
    return;
  }
  const trimmed = args.trim();
  if (!trimmed) {
    if (feedback) feedback.innerHTML = '<div class="ffmpeg-error">Args cannot be empty.</div>';
    return;
  }
  try {
    const res = await fetch(`/api/ffmpeg/profiles/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ args: trimmed })
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    await loadFfmpegProfiles();
    closeEditProfile();
    if (listFeedback) listFeedback.innerHTML = `<div class="ffmpeg-success">Updated profile: <strong>${escapeHtml(name)}</strong></div>`;
  } catch (err) {
    if (feedback) {
      feedback.innerHTML = `<div class=\"ffmpeg-error\">Failed to update profile — ${err}</div>`;
    } else {
      alert("Failed to update profile: " + err);
    }
  }
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

setTimeout(() => {
  const conf = document.getElementById("confirmation-message");
  if (conf) conf.style.display = "none";
  const epgConf = document.getElementById("epg-upload-confirmation");
  if (epgConf) epgConf.style.display = "none";
  const m3uConf = document.getElementById("m3u-upload-confirmation");
  if (m3uConf) m3uConf.style.display = "none";
  const parseEpgConf = document.getElementById("parse-epg-confirmation");
  if (parseEpgConf) parseEpgConf.style.display = "none";
  const uploadEpgStatus = document.getElementById("upload-epg-status");
  if (uploadEpgStatus) uploadEpgStatus.style.display = "none";
  const deleteEpgStatus = document.getElementById("delete-epg-status");
  if (deleteEpgStatus) deleteEpgStatus.style.display = "none";
  const uploadM3UStatus = document.getElementById("upload-m3u-status");
  if (uploadM3UStatus) uploadM3UStatus.style.display = "none";
}, 5000);
