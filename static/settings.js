let epgParsingInProgress = false;
let epgUploadingInProgress = false;
let epgDeletingInProgress = false;
let m3uUploadingInProgress = false;

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

window.addEventListener("DOMContentLoaded", () => {
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
        let html = "<table><thead><tr>";
        html += "<th>Channel Number</th>";
        html += "<th>Channel Name</th>";
        html += "<th>Current Program</th>";
        html += "<th>Subscribers</th>";
        html += "<th>Stream URL</th>";
        html += "<th>Video Codec</th>";
        html += "<th>Resolution</th>";
        html += "<th>Audio Info</th>";
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
                  </tr>`;
        }

        html += "</tbody></table>";
        container.innerHTML = html;
      }
    })
    .catch((error) => {
      document.getElementById("stream-status-content").innerHTML =
        "<p>Error fetching stream status: " + error + "</p>";
    });
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
