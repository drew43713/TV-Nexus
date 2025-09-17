const BASE_URL = "{{ BASE_URL }}";

let statusOverlayEl = null;
let statusOverlayTextEl = null;

function ensureStatusOverlay() {
  if (statusOverlayEl) return;
  statusOverlayEl = document.createElement('div');
  statusOverlayEl.id = 'status-overlay';
  statusOverlayEl.style.position = 'fixed';
  statusOverlayEl.style.inset = '0';
  statusOverlayEl.style.background = 'rgba(0,0,0,0.6)';
  statusOverlayEl.style.display = 'none';
  statusOverlayEl.style.alignItems = 'center';
  statusOverlayEl.style.justifyContent = 'center';
  statusOverlayEl.style.zIndex = '9999';
  statusOverlayEl.style.backdropFilter = 'blur(2px)';

  const box = document.createElement('div');
  box.style.background = '#fff';
  box.style.color = '#111';
  box.style.padding = '20px 24px';
  box.style.borderRadius = '10px';
  box.style.boxShadow = '0 8px 24px rgba(0,0,0,0.3)';
  box.style.minWidth = '280px';
  box.style.maxWidth = '90%';
  box.style.fontFamily = 'system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif';
  box.style.textAlign = 'center';

  const title = document.createElement('div');
  title.textContent = 'Working…';
  title.style.fontSize = '18px';
  title.style.fontWeight = '600';
  title.style.marginBottom = '8px';

  statusOverlayTextEl = document.createElement('div');
  statusOverlayTextEl.textContent = '';
  statusOverlayTextEl.style.fontSize = '14px';
  statusOverlayTextEl.style.whiteSpace = 'pre-line';

  box.appendChild(title);
  box.appendChild(statusOverlayTextEl);
  statusOverlayEl.appendChild(box);
  document.body.appendChild(statusOverlayEl);
}

function showStatusOverlay(message) {
  ensureStatusOverlay();
  statusOverlayTextEl.textContent = message || '';
  statusOverlayEl.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function updateStatusOverlay(message) {
  if (!statusOverlayEl) return;
  statusOverlayTextEl.textContent = message || '';
}

function hideStatusOverlay() {
  if (!statusOverlayEl) return;
  statusOverlayEl.style.display = 'none';
  document.body.style.overflow = '';
}

let _statusOverlayTicker = null;
let _statusOverlayBaseMsg = '';

function startStatusOverlayTicker(baseMessage) {
  _statusOverlayBaseMsg = baseMessage || '';
  let step = 0;
  stopStatusOverlayTicker();
  updateStatusOverlay(_statusOverlayBaseMsg);
  _statusOverlayTicker = setInterval(() => {
    step = (step + 1) % 4; // 0..3
    const dots = '.'.repeat(step);
    updateStatusOverlay(_statusOverlayBaseMsg + dots);
  }, 400);
}

function stopStatusOverlayTicker() {
  if (_statusOverlayTicker) {
    clearInterval(_statusOverlayTicker);
    _statusOverlayTicker = null;
  }
}

// Restore the filter text after page reload, if available.
document.addEventListener("DOMContentLoaded", function () {
  const savedFilterText = localStorage.getItem("filterText");
  if (savedFilterText !== null) {
    const searchInput = document.getElementById("search-input");
    if (searchInput) {
      searchInput.value = savedFilterText;
      filterTable();
    }
    localStorage.removeItem("filterText");
  }

  // Restore scroll position or specific channel row if available
  const restoreChannelId = localStorage.getItem("restoreScrollChannelId");
  const restoreScrollY = localStorage.getItem("restoreScrollY");
  if (restoreChannelId) {
    // Try to scroll the specific row into view
    const tryScrollToRow = () => {
      const row = document.querySelector(`tr[data-channel="${restoreChannelId}"]`);
      if (row) {
        row.scrollIntoView({ block: "center", inline: "nearest" });
      } else if (restoreScrollY) {
        window.scrollTo(0, parseInt(restoreScrollY, 10));
      }
      localStorage.removeItem("restoreScrollChannelId");
      localStorage.removeItem("restoreScrollY");
    };
    // Defer to ensure table has rendered
    requestAnimationFrame(() => setTimeout(tryScrollToRow, 0));
  } else if (restoreScrollY) {
    window.scrollTo(0, parseInt(restoreScrollY, 10));
    localStorage.removeItem("restoreScrollY");
  }
});

document.addEventListener("DOMContentLoaded", function () {
  const toggleStatusBtn = document.getElementById("modal-toggle-status");
  toggleStatusBtn.addEventListener("click", function () {
    if (this.textContent.trim() === "Active") {
      this.textContent = "Inactive";
      this.classList.remove("active");
      this.classList.add("inactive");
    } else {
      this.textContent = "Active";
      this.classList.remove("inactive");
      this.classList.add("active");
    }
  });
  
  // Add event listener for logo search input to filter logos
  document.getElementById("logo-search").addEventListener("input", function(e) {
    displayLogos(e.target.value);
  });

  loadRawFileOptions();
  initEPGSearchWithColors();
});

function updateActiveStatus(checkbox, channelId) {
  const active = checkbox.checked;
  const row = document.querySelector(`tr[data-channel="${channelId}"]`);
  row.setAttribute("data-active", active ? "1" : "0");
  
  if (active) {
    row.cells[5].innerText = "Loading...";
  } else {
    row.cells[5].innerText = "Channel Inactive";
  }

  fetch("/update_channel_active", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `channel_id=${channelId}&active=${active}`
  })
    .then(response => {
      if (!response.ok) {
        return response.json().then(errBody => {
          const msg = errBody.detail || "Unknown error";
          throw new Error(msg);
        });
      }
      return response.json();
    })
    .then(data => {
      if (!data.success) {
        alert("Failed to update active status");
      } else {
        if (active) {
          refreshChannelEpg(channelId);
        }
      }
    })
    .catch(error => {
      alert("Error updating active status: " + error.message);
      checkbox.checked = !active;
      row.setAttribute("data-active", !active ? "1" : "0");
      if (!active) {
        row.cells[5].innerText = "Loading...";
      } else {
        row.cells[5].innerText = "Channel Inactive";
      }
    });
}

function refreshChannelEpg(channelId) {
  const row = document.querySelector(`tr[data-channel="${channelId}"]`);
  let attempts = 0;
  const maxAttempts = 10;
  const pollInterval = 500;
  const poller = setInterval(() => {
    attempts++;
    fetch(`/api/current_program?channel_id=${channelId}`, { cache: "no-cache" })
      .then(response => response.json())
      .then(data => {
        let newEpgText = data.title ? data.title : "No Program";
        if (row) {
          row.cells[5].innerText = newEpgText;
        }
        if (newEpgText !== "Loading..." || attempts >= maxAttempts) {
          clearInterval(poller);
        }
      })
      .catch(error => {
        console.error("Error refreshing EPG data for channel", channelId, error);
        clearInterval(poller);
      });
  }, pollInterval);
}

function bulkUpdateActive(active) {
  const checkboxes = document.querySelectorAll(".active-checkbox");
  let selectedIds = [];
  checkboxes.forEach(cb => {
    if (cb.checked && cb.closest("tr").style.display !== "none") {
      selectedIds.push(cb.closest("tr").getAttribute("data-channel"));
    }
  });
  if (selectedIds.length === 0) {
    alert("No channels selected for bulk update.");
    return;
  }
  const channelIds = selectedIds.join(",");
  fetch("/update_channels_active_bulk", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `channel_ids=${channelIds}&active=${active}`
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        checkboxes.forEach(cb => {
          if (selectedIds.includes(cb.closest("tr").getAttribute("data-channel"))) {
            cb.checked = active;
          }
        });
        alert("Bulk update successful.");
      } else {
        alert("Bulk update failed.");
      }
    })
    .catch(error => alert("Bulk update error: " + error));
}

function toggleSelectAll(master) {
  const checkboxes = document.querySelectorAll(".active-checkbox");
  checkboxes.forEach(cb => {
    if (cb.closest("tr").style.display !== "none") {
      cb.checked = master.checked;
    }
  });
}

let logoPickerTargetChannelId = null;
let selectedLogo = "";
let allLogos = [];
let editedChannelName = "";
let editedChannelCategory = "";
let currentChannelId = null;

function openLogoPickerForChannel(channelId, currentLogo) {
  logoPickerTargetChannelId = channelId;
  selectedLogo = currentLogo;
  fetch("/api/logos")
    .then(response => response.json())
    .then(data => {
      allLogos = data;
      document.getElementById("logo-search").value = "";
      displayLogos("");
      document.getElementById("logo-picker-overlay").style.display = "block";
      document.getElementById("logo-picker-modal").style.display = "block";
      document.getElementById("logo-picker-modal").scrollTop = 0;
      document.getElementById("logo-search").focus();
    })
    .catch(error => console.error("Error fetching logos:", error));
}

function openLogoPicker() {
  logoPickerTargetChannelId = null;
  fetch("/api/logos")
    .then(response => response.json())
    .then(data => {
      allLogos = data;
      document.getElementById("logo-search").value = "";
      displayLogos("");
      document.getElementById("logo-picker-overlay").style.display = "block";
      document.getElementById("logo-picker-modal").style.display = "block";
      document.getElementById("logo-picker-modal").scrollTop = 0;
      document.getElementById("logo-search").focus();
    })
    .catch(error => console.error("Error fetching logos:", error));
}

function displayLogos(filterText) {
  const pickerContainer = document.getElementById("logo-picker-container");
  pickerContainer.innerHTML = "";
  const lowerFilter = filterText.toLowerCase();
  allLogos.forEach(logoUrl => {
    const filename = logoUrl.split("/").pop().toLowerCase();
    if (filename.includes(lowerFilter)) {
      const logoImg = document.createElement("img");
      logoImg.src = logoUrl;
      if (logoUrl === selectedLogo) logoImg.classList.add("selected");
      logoImg.onclick = function(e) {
        e.stopPropagation();
        selectedLogo = logoUrl;
        if (logoPickerTargetChannelId !== null) {
          let formData = new FormData();
          formData.append("channel_id", logoPickerTargetChannelId);
          formData.append("new_logo", logoUrl);
          fetch("/update_channel_logo", {
            method: "POST",
            body: formData
          })
            .then(response => response.json())
            .then(data => {
              if (data.success) {
                let row = document.querySelector(`tr[data-channel="${logoPickerTargetChannelId}"]`);
                if (row) {
                  let img = row.querySelector("img.channel-logo");
                  if (img) {
                    img.src = logoUrl;
                  }
                }
              } else {
                alert("Failed to update channel logo: " + data.error);
              }
            })
            .catch(error => alert("Error updating channel logo: " + error))
            .finally(() => {
              logoPickerTargetChannelId = null;
              closeLogoPicker();
            });
        } else {
          document.getElementById("modal-logo").src = logoUrl;
          closeLogoPicker();
        }
      };
      pickerContainer.appendChild(logoImg);
    }
  });
}

function getChannelIdsFromUI() {
  const tableRows = document.querySelectorAll('#channels-table tbody tr');
  let ids = [];
  tableRows.forEach(row => {
    if (row.style.display !== "none") {
      ids.push(row.getAttribute("data-channel"));
    }
  });
  return ids.join(",");
}

function autoNumberChannels() {
  const startNumber = document.getElementById("start_number").value;
  const channelIds = getChannelIdsFromUI();
  if (!startNumber) {
    alert("Please enter a valid starting number.");
    return;
  }
  const formData = new FormData();
  formData.append("start_number", startNumber);
  formData.append("channel_ids", channelIds);
  fetch("/auto_number_channels", {
    method: "POST",
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        alert("Channels renumbered successfully!");
        localStorage.setItem("filterText", document.getElementById("search-input").value);
        const url = new URL(window.location.href);
        url.searchParams.set('_ts', Date.now().toString());
        window.location.replace(url.toString());
      } else {
        alert("Error: " + data.message);
      }
    })
    .catch(error => {
      alert("Request failed: " + error);
    });
}

function loadRawFileOptions() {
  fetch("/api/epg_filenames")
    .then(response => response.json())
    .then(filenames => {
      const select = document.getElementById("raw-file-filter");
      select.innerHTML = '<option value="">All Files</option>';
      filenames.forEach(fn => {
        const option = document.createElement("option");
        option.value = fn;
        option.textContent = fn;
        select.appendChild(option);
      });
    })
    .catch(error => console.error("Error fetching raw file list:", error));
}

function initEPGSearchWithColors() {
  const epgInput = document.getElementById("epg-input");
  const suggestionsBox = document.getElementById("epg-suggestions");
  const rawFileSelect = document.getElementById("raw-file-filter");
  
  if (!epgInput || !suggestionsBox) return;
  
  epgInput.addEventListener("input", function () {
    const searchTerm = epgInput.value.trim();
    if (searchTerm.length < 2) {
      suggestionsBox.style.display = "none";
      return;
    }
    const rawFile = rawFileSelect.value;
    const url = `/api/epg_entries?search=${encodeURIComponent(searchTerm)}&raw_file=${encodeURIComponent(rawFile)}`;
    fetch(url)
      .then(response => response.json())
      .then(entries => {
        suggestionsBox.innerHTML = "";
        if (!entries || entries.length === 0) {
          suggestionsBox.style.display = "none";
          return;
        }
        entries.forEach(item => {
          const div = document.createElement("div");
          div.className = "epg-suggestion-item";
          div.innerHTML = `
            <div class="epg-color-dot" style="background: ${item.color || '#fff'};"></div>
            <span>${item.display_name}</span>
          `;
          div.addEventListener("click", () => {
            epgInput.value = item.display_name;
            suggestionsBox.style.display = "none";
          });
          suggestionsBox.appendChild(div);
        });
        suggestionsBox.style.display = "block";
      })
      .catch(err => console.error("Error fetching EPG entries with colors:", err));
  });
  
  document.addEventListener("click", (event) => {
    if (!suggestionsBox.contains(event.target) && event.target !== epgInput) {
      suggestionsBox.style.display = "none";
    }
  });
  
  rawFileSelect.addEventListener("change", function () {
    epgInput.dispatchEvent(new Event("input"));
  });
}

function closeModal() {
  document.getElementById("modal").style.display = "none";
  document.getElementById("overlay").style.display = "none";
}

function closeLogoPicker() {
  document.getElementById("logo-picker-overlay").style.display = "none";
  document.getElementById("logo-picker-modal").style.display = "none";
}

function handleEnter(event, input) {
  if (event.key === "Enter") {
    input.blur();
  }
}

function handleEditButtonClick(event, button) {
  event.stopPropagation();
  const row = button.closest("tr");
  const channelId = row.getAttribute("data-channel");
  const channelNumber = row.getAttribute("data-channel-number");
  const channelName = row.getAttribute("data-name");
  const currentLogoUrl = row.getAttribute("data-logo");
  const channelCategory = row.getAttribute("data-category");
  const epgTitle = row.getAttribute("data-epg");
  const epgEntry = row.getAttribute("data-epg-entry");
  const streamUrl = row.getAttribute("data-stream-url");
  
  currentChannelId = channelId;
  selectedLogo = currentLogoUrl;
  editedChannelName = channelName;
  editedChannelCategory = channelCategory;
  
  const modalTitle = document.getElementById("modal-title");
  modalTitle.innerText = channelName;
  modalTitle.onclick = editChannelName;
  
  const modalCategory = document.getElementById("modal-category-text");
  modalCategory.innerText = "Category: " + channelCategory;
  modalCategory.onclick = editChannelCategory;
  
  document.getElementById("modal-logo").src = currentLogoUrl;
  document.getElementById("modal-channel-number").value = channelNumber;
  document.getElementById("modal-epg").innerText = epgTitle;
  document.getElementById("modal-stream-url").innerText = streamUrl;
  document.getElementById("modal-stream-url").href = streamUrl;
  
  document.getElementById("epg-input").value = epgEntry || "";
  document.getElementById("probe-results").innerText = "";
  
  let activeState = row.getAttribute("data-active");
  const toggleStatusBtn = document.getElementById("modal-toggle-status");
  if (activeState === "1") {
    toggleStatusBtn.textContent = "Active";
    toggleStatusBtn.classList.remove("inactive");
    toggleStatusBtn.classList.add("active");
  } else {
    toggleStatusBtn.textContent = "Inactive";
    toggleStatusBtn.classList.remove("active");
    toggleStatusBtn.classList.add("inactive");
  }
  
  openModal();
}

function openModal() {
  document.getElementById("modal").style.display = "block";
  document.getElementById("overlay").style.display = "block";
}

function editChannelName() {
  const modalTitle = document.getElementById("modal-title");
  const currentName = modalTitle.innerText;
  const input = document.createElement("input");
  input.type = "text";
  input.id = "modal-channel-name-inline";
  input.value = currentName;
  input.style.fontSize = "inherit";
  input.style.fontFamily = "inherit";
  input.style.textAlign = "center";
  input.onblur = saveChannelNameInline;
  input.onkeydown = function (e) {
    if (e.key === "Enter") input.blur();
  };
  modalTitle.parentNode.replaceChild(input, modalTitle);
  input.focus();
  input.select();
}

function saveChannelNameInline() {
  const input = document.getElementById("modal-channel-name-inline");
  if (!input) return;
  const newName = input.value;
  editedChannelName = newName;
  const h2 = document.createElement("h2");
  h2.id = "modal-title";
  h2.innerText = newName;
  h2.onclick = editChannelName;
  input.parentNode.replaceChild(h2, input);
}

function editChannelCategory() {
  const modalCategory = document.getElementById("modal-category-text");
  const currentCategory = modalCategory.innerText.replace(/^Category:\s*/, "");
  const input = document.createElement("input");
  input.type = "text";
  input.id = "modal-category-inline";
  input.value = currentCategory;
  input.setAttribute("list", "category-list");
  input.style.fontSize = "inherit";
  input.style.fontFamily = "inherit";
  input.style.textAlign = "center";
  input.onblur = saveChannelCategoryInline;
  input.onkeydown = function (e) {
    if (e.key === "Enter") input.blur();
  };
  modalCategory.parentNode.replaceChild(input, modalCategory);
  input.focus();
  input.select();
  loadAvailableCategories();
}

function loadAvailableCategories() {
  fetch("/api/categories")
    .then(response => response.json())
    .then(data => {
      let datalist = document.getElementById("category-list");
      if (!datalist) {
        datalist = document.createElement("datalist");
        datalist.id = "category-list";
        document.body.appendChild(datalist);
      }
      datalist.innerHTML = "";
      data.forEach(cat => {
        const option = document.createElement("option");
        option.value = cat;
        datalist.appendChild(option);
      });
    })
    .catch(error => console.error("Error loading categories:", error));
}

function saveChannelCategoryInline() {
  const input = document.getElementById("modal-category-inline");
  if (!input) return;
  const newCategory = input.value;
  editedChannelCategory = newCategory;
  const p = document.createElement("p");
  p.id = "modal-category-text";
  p.innerText = "Category: " + newCategory;
  p.onclick = editChannelCategory;
  input.parentNode.replaceChild(p, input);
}

function updateModalChannelNumber(input) {}

function updateChannelNumber(input) {
  let oldValue = input.getAttribute("data-old-value");
  let newValue = input.value.trim();
  if (oldValue === newValue || newValue === "") {
    input.value = oldValue;
    return Promise.resolve(false);
  }
  let formData = new FormData();
  formData.append("current_id", oldValue);
  formData.append("new_id", newValue);
  return fetch("/update_channel_number", { method: "POST", body: formData })
    .then(response => {
      if (!response.ok) {
        return response.json().then(errBody => {
          throw new Error(errBody.detail || "Unknown error");
        });
      }
      // Update the data-old-value so subsequent edits compare against the new value
      input.setAttribute("data-old-value", newValue);
      // Update the containing row's data attributes so other features (like the modal) see the new number
      const row = input.closest('tr');
      if (row) {
        row.setAttribute('data-channel-number', newValue);
        // If the cell has any mirrored text nodes for the number, update them here if needed.
        // (Assumes the input displays the number; no extra text update required.)
        row.classList.add('saved-flash');
        setTimeout(() => row.classList.remove('saved-flash'), 600);
      }
      return true;
    })
    .catch(error => {
      alert("Error updating channel number: " + error.message);
      input.value = oldValue;
      return false;
    });
}

// Fetch fresh page HTML and replace only the channels table body, preserving filter and scroll
function refreshTableBodyPreservingState(focusChannelId) {
  const table = document.getElementById('channels-table');
  if (!table) return;
  const oldTbody = table.querySelector('tbody');
  if (!oldTbody) return;

  const searchInput = document.getElementById('search-input');
  const currentFilter = searchInput ? searchInput.value : '';
  const prevScrollY = window.scrollY;

  // Measure anchor position (the edited row) relative to viewport before replacement
  let anchorTop = null;
  if (focusChannelId) {
    const anchorRow = document.querySelector(`tr[data-channel="${focusChannelId}"]`);
    if (anchorRow) {
      const rect = anchorRow.getBoundingClientRect();
      anchorTop = rect.top;
    }
  }

  const url = new URL(window.location.href);
  url.searchParams.set('_ts', Date.now().toString());

  fetch(url.toString(), { cache: 'no-store' })
    .then(resp => resp.text())
    .then(html => {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      const newTbody = doc.querySelector('#channels-table tbody');
      if (!newTbody) return;

      // Replace tbody
      oldTbody.parentNode.replaceChild(newTbody, oldTbody);

      // Re-apply filter after replacement
      if (searchInput) {
        searchInput.value = currentFilter || '';
        if (currentFilter) {
          filterTable();
        }
      }

      // Disable smooth scroll to avoid animated jumps during correction
      const prevBehavior = document.documentElement.style.scrollBehavior;
      document.documentElement.style.scrollBehavior = 'auto';

      if (focusChannelId) {
        const newAnchor = document.querySelector(`tr[data-channel="${focusChannelId}"]`);
        if (newAnchor && anchorTop !== null) {
          const newRect = newAnchor.getBoundingClientRect();
          const delta = newRect.top - anchorTop;
          if (Math.abs(delta) > 1) {
            window.scrollBy(0, delta);
          }
        } else {
          window.scrollTo(0, prevScrollY);
        }
      } else {
        window.scrollTo(0, prevScrollY);
      }

      // Restore previous scroll behavior
      document.documentElement.style.scrollBehavior = prevBehavior;
    })
    .catch(err => {
      console.error('Partial table refresh failed:', err);
    });
}

function saveAllChanges() {
  if (!currentChannelId) {
    alert("Channel ID not set.");
    return;
  }
  const modalChannelNumberInput = document.getElementById("modal-channel-number");
  const updatedChannelNumber = modalChannelNumberInput.value;
  const epgEntry = document.getElementById("epg-input").value;
  const formData = new FormData();
  formData.append("channel_id", currentChannelId);
  formData.append("new_channel_number", updatedChannelNumber);
  formData.append("new_name", editedChannelName);
  formData.append("new_category", editedChannelCategory);
  formData.append("new_logo", selectedLogo);
  formData.append("new_epg_entry", epgEntry);
  
  const toggleStatusBtn = document.getElementById("modal-toggle-status");
  const newActive = toggleStatusBtn.textContent.trim() === "Active" ? "1" : "0";
  formData.append("new_active", newActive);
  
  fetch("/update_channel_properties", {
    method: "POST",
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        alert("Channel properties and EPG entry updated successfully!");
        if (newActive === "1") {
          refreshChannelEpg(currentChannelId);
        }
        // Refresh only the table body and keep the edited row anchored
        refreshTableBodyPreservingState(currentChannelId);
        // Close the modal without navigating away
        closeModal();
      } else {
        alert("Failed to update channel properties: " + data.error);
      }
    })
    .catch(error => {
      alert("Error updating channel properties: " + error);
      console.error("Error:", error);
    });
}

function deleteChannel() {
  if (!currentChannelId) {
    alert("Channel ID not set.");
    return;
  }
  if (!confirm("Are you sure you want to permanently delete this channel? This action cannot be undone.")) {
    return;
  }
  const formData = new FormData();
  formData.append("channel_id", currentChannelId);
  fetch("/delete_channel", {
    method: "POST",
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        alert(data.message);
        const url = new URL(window.location.href);
        url.searchParams.set('_ts', Date.now().toString());
        window.location.replace(url.toString());
      } else {
        alert("Failed to delete channel: " + data.error);
      }
    })
    .catch(error => {
      alert("Error deleting channel: " + error);
    });
}

function probeStream() {
  if (!currentChannelId) {
    alert("Channel ID not set");
    return;
  }
  const probeDiv = document.getElementById("probe-results");
  probeDiv.innerText = "Probing stream, please wait...";
  fetch(`/probe_stream?channel_id=${currentChannelId}`)
    .then(response => response.json())
    .then(data => {
      probeDiv.innerText = JSON.stringify(data, null, 2);
    })
    .catch(error => {
      probeDiv.innerText = "Error probing stream: " + error;
      console.error("Error probing stream:", error);
    });
}

document.addEventListener("DOMContentLoaded", function () {
  const table = document.getElementById("channels-table");
  if (!table) return;

  // Add "Insert Channel…" button near the search bar
  const searchInput = document.getElementById('search-input');
  if (searchInput && !document.getElementById('insert-channel-btn')) {
    const btn = document.createElement('button');
    btn.id = 'insert-channel-btn';
    btn.type = 'button';
    btn.textContent = 'Insert Channel…';
    btn.style.marginLeft = '8px';
    btn.addEventListener('click', insertChannelAtPrompt);
    // Place the button right after the search input
    searchInput.insertAdjacentElement('afterend', btn);
  }

  const headers = table.querySelectorAll("thead th");
  headers.forEach((header, index) => {
    header.addEventListener("click", () => {
      sortTableByColumn(table, index);
    });
  });
  
  // Commit inline edits when pressing Enter inside inputs in the table
  table.addEventListener("keydown", function(e) {
    const target = e.target;
    if (e.key === "Enter" && target && target.tagName === "INPUT") {
      e.preventDefault();
      const inputEl = target;
      const row = inputEl.closest('tr');
      const channelId = row ? row.getAttribute('data-channel') : null;
      // Mark as just-saved to prevent duplicate save on subsequent blur
      inputEl.dataset.justSaved = '1';
      Promise.resolve(updateChannelNumber(inputEl)).then((success) => {
        // Clear the marker shortly after
        setTimeout(() => { delete inputEl.dataset.justSaved; }, 200);
        if (success) {
          // Replace only the table body with fresh content to reflect server-side changes
          refreshTableBodyPreservingState(channelId);
        } else {
          // Exit editing gracefully
          inputEl.blur();
        }
      });
    }
  });

  table.addEventListener("focusout", function(e) {
    const target = e.target;
    if (target && target.tagName === "INPUT") {
      // If Enter just triggered a save, skip saving again on blur
      if (target.dataset.justSaved === '1') return;
      // Fire and forget; no reload on blur
      Promise.resolve(updateChannelNumber(target));
    }
  });
  
  function getCellValue(cell) {
    const input = cell.querySelector("input");
    return input ? input.value.trim() : cell.innerText.trim();
  }
  
  function sortTableByColumn(table, columnIndex) {
    const tbody = table.querySelector("tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));
    const header = table.querySelectorAll("thead th")[columnIndex];
    let ascending = header.getAttribute("data-sort-asc") === "true";
    ascending = !ascending;
    header.setAttribute("data-sort-asc", ascending);
    table.querySelectorAll("thead th").forEach((th, idx) => {
      if (idx !== columnIndex) th.removeAttribute("data-sort-asc");
    });
    rows.sort((a, b) => {
      let cellA = getCellValue(a.cells[columnIndex]);
      let cellB = getCellValue(b.cells[columnIndex]);
      const numA = parseFloat(cellA.replace(/[^0-9.-]+/g, ""));
      const numB = parseFloat(cellB.replace(/[^0-9.-]+/g, ""));
      if (!isNaN(numA) && !isNaN(numB)) {
        return ascending ? numA - numB : numB - numA;
      }
      return ascending ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
    });
    rows.forEach(row => tbody.appendChild(row));
  }
});

function filterTable() {
  const input = document.getElementById("search-input");
  const filter = input.value.toLowerCase();
  const table = document.getElementById("channels-table");
  const tr = table.getElementsByTagName("tr");
  for (let i = 1; i < tr.length; i++) {
    const tds = tr[i].getElementsByTagName("td");
    let rowContainsQuery = false;
    for (let j = 0; j < tds.length; j++) {
      const cellText = tds[j].textContent || tds[j].innerText;
      if (cellText.toLowerCase().indexOf(filter) > -1) {
        rowContainsQuery = true;
        break;
      }
    }
    tr[i].style.display = rowContainsQuery ? "" : "none";
  }
}

function apiUpdateChannelNumber(currentId, newId) {
  const formData = new FormData();
  formData.append('current_id', String(currentId));
  formData.append('new_id', String(newId));
  // Include swap=false if backend supports it; ignored otherwise
  formData.append('swap', 'false');
  return fetch('/update_channel_number', { method: 'POST', body: formData })
    .then(response => {
      if (!response.ok) {
        return response.json().then(errBody => {
          throw new Error(errBody.detail || 'Unknown error');
        });
      }
      return true;
    });
}

// New function as per instructions:
function apiInsertChannelAt(insertAt) {
  const formData = new FormData();
  formData.append('insert_at', String(insertAt));
  // Hint to backend about non-swapping behavior if supported
  formData.append('swap', 'false');
  return fetch('/insert_channel_at', { method: 'POST', body: formData })
    .then(async (response) => {
      if (!response.ok) {
        // try to parse JSON error if present
        let msg = 'Request failed';
        try {
          const data = await response.json();
          msg = data && (data.detail || data.error || msg);
        } catch (_) {}
        const err = new Error(msg);
        err.status = response.status;
        throw err;
      }
      return response.json();
    });
}

async function performInsertAtServer(insertAt) {
  const table = document.getElementById('channels-table');
  let total = 0;
  if (table) {
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    for (const row of rows) {
      const numStr = row.getAttribute('data-channel-number');
      if (!numStr) continue;
      const num = parseInt(numStr, 10);
      if (!Number.isFinite(num)) continue;
      if (num >= insertAt) total++;
    }
  }

  const baseMsg = (total > 0)
    ? ('Shifting ' + total + ' channel' + (total === 1 ? '' : 's') + ' at and above ' + insertAt)
    : ('Shifting channels at and above ' + insertAt);
  showStatusOverlay(baseMsg);
  startStatusOverlayTicker(baseMsg + ' ');

  try {
    const result = await apiInsertChannelAt(insertAt);
    if (result && result.success === false) {
      throw new Error(result.error || 'Insert failed');
    }
    stopStatusOverlayTicker();
    updateStatusOverlay('Refreshing table…');
    await new Promise(r => setTimeout(r, 50));
    refreshTableBodyPreservingState();
  } catch (err) {
    stopStatusOverlayTicker();
    alert('Insert failed: ' + (err && err.message ? err.message : err));
  } finally {
    stopStatusOverlayTicker();
    hideStatusOverlay();
  }
}

async function performInsertAt(insertAt) {
  const table = document.getElementById('channels-table');
  if (!table) {
    alert('Channels table not found.');
    return;
  }
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  // Collect all numeric channel numbers >= insertAt
  const targets = [];
  for (const row of rows) {
    const numStr = row.getAttribute('data-channel-number');
    if (!numStr) continue;
    const num = parseInt(numStr, 10);
    if (!Number.isFinite(num)) continue;
    if (num >= insertAt) targets.push(num);
  }
  if (targets.length === 0) {
    alert('No channels at or above that number to shift.');
    return;
  }
  // Sort descending to avoid collisions when incrementing
  targets.sort((a, b) => b - a);

  const total = targets.length;
  showStatusOverlay('Shifting ' + total + ' channel' + (total === 1 ? '' : 's') + ' at and above ' + insertAt);
  // No spinner here because we show fine-grained per-item progress below.

  try {
    let processed = 0;
    for (const current of targets) {
      processed++;
      updateStatusOverlay(`Shifting channel ${current} ➜ ${current + 1} (${processed}/${total})…`);
      await apiUpdateChannelNumber(current, current + 1);
    }
    updateStatusOverlay('Refreshing table…');
    // Refresh only the table body; keep scroll position
    await new Promise(resolve => setTimeout(resolve, 50));
    refreshTableBodyPreservingState();
  } catch (err) {
    console.error('Insert operation failed:', err);
    alert('Insert failed: ' + (err && err.message ? err.message : err));
  } finally {
    hideStatusOverlay();
  }
}

// New streaming function inserted after performInsertAtServer:
function performInsertAtStream(insertAt) {
  // If EventSource isn't supported, fallback immediately
  if (typeof EventSource === 'undefined') {
    console.warn('EventSource not supported; falling back to non-streaming insert.');
    return performInsertAtServer(insertAt);
  }

  const url = `/insert_channel_at_stream?insert_at=${encodeURIComponent(insertAt)}`;
  const es = new EventSource(url);

  let lastLine = '';
  let tickerActive = false;
  const show = (msg) => { updateStatusOverlay(msg); };
  const restartTickerWith = (msg) => {
    stopStatusOverlayTicker();
    startStatusOverlayTicker((msg || '').trim() + ' ');
    tickerActive = true;
  };

  showStatusOverlay(`Preparing to shift at and above ${insertAt}…`);
  restartTickerWith(`Preparing to shift at and above ${insertAt}…`);

  es.onmessage = (ev) => {
    if (!ev || typeof ev.data !== 'string' || !ev.data.length) return;
    // Only keep the most recent line on screen
    lastLine = ev.data;
    show(lastLine);
    // Keep the UI feeling live by animating dots after the latest line
    restartTickerWith(lastLine);
  };

  es.addEventListener('done', (ev) => {
    stopStatusOverlayTicker();
    try {
      if (ev && ev.data) {
        // Optionally parse payload
        JSON.parse(ev.data);
      }
    } catch (_) {}
    es.close();
    // Refresh table after completion
    refreshTableBodyPreservingState();
    hideStatusOverlay();
  });

  es.addEventListener('error', (ev) => {
    stopStatusOverlayTicker();
    const msg = ev && ev.data ? ev.data : 'Insert stream error';
    console.error('Insert stream error:', msg);
    es.close();
    hideStatusOverlay();
    // Fallback to non-streaming server call so operation still completes
    performInsertAtServer(insertAt).catch((err) => {
      alert('Insert failed: ' + (err && err.message ? err.message : err));
    });
  });

  // Network-level error
  es.onerror = () => {
    stopStatusOverlayTicker();
    console.warn('EventSource network error; falling back.');
    try { es.close(); } catch (_) {}
    hideStatusOverlay();
    performInsertAtServer(insertAt).catch((err) => {
      alert('Insert failed: ' + (err && err.message ? err.message : err));
    });
  };
}

// Modified insertChannelAtPrompt as per instructions:
function insertChannelAtPrompt() {
  const value = prompt('Enter the channel number to insert before (e.g., 100):');
  if (value === null) return; // user cancelled
  const n = parseInt(String(value).trim(), 10);
  if (!Number.isFinite(n) || n < 0) {
    alert('Please enter a valid non-negative integer channel number.');
    return;
  }
  // Prefer streaming progress for per-item updates
  try {
    performInsertAtStream(n);
  } catch (e) {
    // Safety fallback
    console.warn('Streaming insert failed to start, falling back:', e);
    performInsertAtServer(n).catch((err) => {
      if (err && (err.status === 404 || err.message === 'Failed to fetch')) {
        performInsertAt(n);
      } else {
        alert('Insert failed: ' + (err && err.message ? err.message : err));
      }
    });
  }
}

