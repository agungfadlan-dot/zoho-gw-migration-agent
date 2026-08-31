/**
 * Zoho to Google Workspace Migration Agent - Web UI Controller
 */

document.addEventListener("DOMContentLoaded", () => {
  // Global UI State
  let currentStep = 1;
  let saJsonContent = "";
  let saFileName = "";
  let discoveredUsers = [];
  let recommendedPilotEmails = [];
  let selectedUserEmails = new Set();
  let eventSource = null;
  let pollInterval = null;

  // --- Elements ---
  const stepItems = document.querySelectorAll(".step-item");
  const stepContents = document.querySelectorAll(".step-content");
  const alertContainer = document.getElementById("alert-container");

  // Step 1 Elements
  const formCredentials = document.getElementById("form-credentials");
  const saDropzone = document.getElementById("sa-dropzone");
  const saFileInput = document.getElementById("sa-file-input");
  const dropzoneText = document.getElementById("dropzone-text");
  const saFilenameDisplay = document.getElementById("sa-filename-display");
  const btnSaveCredentials = document.getElementById("btn-save-credentials");

  // Step 2 Elements
  const preflightSpinner = document.getElementById("preflight-spinner");
  const preflightResultIcon = document.getElementById("preflight-result-icon");
  const preflightTitle = document.getElementById("preflight-title");
  const preflightSubtitle = document.getElementById("preflight-subtitle");
  const preflightDetails = document.getElementById("preflight-details");
  const btnRetestPreflight = document.getElementById("btn-retest-preflight");
  const btnProceedDiscovery = document.getElementById("btn-proceed-discovery");

  // Step 3 Elements
  const discoveryLoading = document.getElementById("discovery-loading");
  const discoveryDashboard = document.getElementById("discovery-dashboard");
  const metricUsers = document.getElementById("metric-users");
  const metricMessages = document.getElementById("metric-messages");
  const metricSize = document.getElementById("metric-size");
  const metricItems = document.getElementById("metric-items");
  const pilotRecommendationText = document.getElementById("pilot-recommendation-text");
  const btnProceedScope = document.getElementById("btn-proceed-scope");

  // Step 4 Elements
  const presetButtons = document.querySelectorAll(".btn-preset");
  const chkDryRun = document.getElementById("chk-dry-run");
  const selectionCountBadge = document.getElementById("selection-count-badge");
  const userTableSearch = document.getElementById("user-table-search");
  const userTableBody = document.getElementById("user-table-body");
  const thSelectAll = document.getElementById("th-select-all");
  const btnSelectAll = document.getElementById("btn-select-all");
  const btnDeselectAll = document.getElementById("btn-deselect-all");
  const btnStartMigration = document.getElementById("btn-start-migration");

  // Step 5 Elements
  const activeStagePill = document.getElementById("active-stage-pill");
  const pausedStatePill = document.getElementById("paused-state-pill");
  const progressDetailText = document.getElementById("progress-detail-text");
  const progressPctValue = document.getElementById("progress-pct-value");
  const progressBarFill = document.getElementById("progress-bar-fill");
  const statActiveUser = document.getElementById("stat-active-user");
  const statItemsCount = document.getElementById("stat-items-count");
  const statThroughput = document.getElementById("stat-throughput");
  const logTerminal = document.getElementById("log-terminal");
  const completionSummaryCard = document.getElementById("completion-summary-card");
  const completionSummaryText = document.getElementById("completion-summary-text");
  const btnNewMigration = document.getElementById("btn-new-migration");
  const btnResetSession = document.getElementById("btn-reset-session");
  const networkWarningBanner = document.getElementById("network-warning-banner");
  const pauseNoticeBanner = document.getElementById("pause-notice-banner");
  const btnPauseMigration = document.getElementById("btn-pause-migration");
  const btnResumeMigration = document.getElementById("btn-resume-migration");
  const btnCancelMigration = document.getElementById("btn-cancel-migration");

  // --- Step Navigation Helper ---
  function navigateToStep(step) {
    currentStep = step;
    stepItems.forEach(item => {
      const s = parseInt(item.getAttribute("data-step"));
      item.classList.remove("active", "completed");
      if (s === step) item.classList.add("active");
      else if (s < step) item.classList.add("completed");
    });

    stepContents.forEach(content => {
      content.classList.remove("active");
    });
    const targetContent = document.getElementById(`step-${step}`);
    if (targetContent) targetContent.classList.add("active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // Back button event delegation
  document.querySelectorAll(".btn-back").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = parseInt(btn.getAttribute("data-target"));
      if (target) navigateToStep(target);
    });
  });

  // Alerts
  function showAlert(message, type = "danger") {
    alertContainer.innerHTML = `
      <div class="card alert-box alert-${type}" style="margin-bottom: 20px;">
        <div class="alert-icon">${type === "success" ? "✅" : "⚠️"}</div>
        <div>
          <h4 class="alert-title">${type === "success" ? "Success" : "Notice"}</h4>
          <p>${message}</p>
        </div>
      </div>
    `;
    setTimeout(() => {
      alertContainer.innerHTML = "";
    }, 6000);
  }

  // Password Visibility Toggles
  document.querySelectorAll(".btn-toggle-pass").forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target");
      const input = document.getElementById(targetId);
      if (input.type === "password") {
        input.type = "text";
        btn.textContent = "🔒";
      } else {
        input.type = "password";
        btn.textContent = "👁️";
      }
    });
  });

  // --- File Dropzone Logic (Google SA JSON) ---
  saDropzone.addEventListener("click", () => saFileInput.click());

  saDropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    saDropzone.classList.add("dragover");
  });

  saDropzone.addEventListener("dragleave", () => {
    saDropzone.classList.remove("dragover");
  });

  saDropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    saDropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleSaFile(e.dataTransfer.files[0]);
    }
  });

  saFileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      handleSaFile(e.target.files[0]);
    }
  });

  function handleSaFile(file) {
    if (!file.name.endsWith(".json")) {
      showAlert("Please upload a valid Google Service Account .json key file.");
      return;
    }
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const parsed = JSON.parse(event.target.result);
        if (!parsed.client_email || !parsed.private_key) {
          showAlert("Invalid Service Account JSON format: missing client_email or private_key.");
          return;
        }
        saJsonContent = event.target.result;
        saFileName = file.name;
        dropzoneText.style.display = "none";
        saFilenameDisplay.style.display = "inline-block";
        saFilenameDisplay.textContent = `✓ Loaded: ${file.name} (${parsed.client_email})`;
      } catch (err) {
        showAlert("Failed to parse JSON file: " + err.message);
      }
    };
    reader.readAsText(file);
  }

  // --- Step 1: Save Credentials & Trigger Pre-Flight ---
  btnSaveCredentials.addEventListener("click", async (e) => {
    e.preventDefault();
    const zohoDomain = document.getElementById("zoho-domain").value;
    const zohoClientId = document.getElementById("zoho-client-id").value.trim();
    const zohoClientSecret = document.getElementById("zoho-client-secret").value.trim();
    const zohoRefreshToken = document.getElementById("zoho-refresh-token").value.trim();
    const googleAdminEmail = document.getElementById("google-admin-email").value.trim();
    const checkpointDb = document.getElementById("checkpoint-db").value.trim();

    if (!zohoClientId || !zohoClientSecret || !zohoRefreshToken) {
      showAlert("Please provide all required Zoho OAuth credentials.");
      return;
    }
    if (!googleAdminEmail) {
      showAlert("Please enter your Google Workspace Super Admin Email.");
      return;
    }
    if (!saJsonContent) {
      showAlert("Please upload your Google Service Account JSON key file.");
      return;
    }

    btnSaveCredentials.disabled = true;
    btnSaveCredentials.textContent = "Storing in Encrypted Vault...";

    try {
      const resp = await fetch("/api/vault/store", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          zoho_domain: zohoDomain,
          zoho_client_id: zohoClientId,
          zoho_client_secret: zohoClientSecret,
          zoho_refresh_token: zohoRefreshToken,
          google_admin_email: googleAdminEmail,
          google_sa_json: saJsonContent,
          checkpoint_db: checkpointDb
        })
      });
      const data = await resp.json();
      if (!data.success) {
        showAlert(data.error || "Failed to store credentials.");
        btnSaveCredentials.disabled = false;
        btnSaveCredentials.textContent = "Save & Run Security Pre-Flight ➜";
        return;
      }

      navigateToStep(2);
      runPreflightCheck();
    } catch (err) {
      showAlert("Error connecting to local server: " + err.message);
    } finally {
      btnSaveCredentials.disabled = false;
      btnSaveCredentials.textContent = "Save & Run Security Pre-Flight ➜";
    }
  });

  // --- Step 2: Pre-Flight Diagnostics ---
  async function runPreflightCheck() {
    preflightSpinner.style.display = "block";
    preflightResultIcon.style.display = "none";
    preflightTitle.textContent = "Running Security Diagnostics...";
    preflightSubtitle.textContent = "Auditing scopes, regional endpoints, and DWD certificates";
    preflightDetails.style.display = "none";
    preflightDetails.innerHTML = "";
    btnProceedDiscovery.disabled = true;

    try {
      const resp = await fetch("/api/preflight", { method: "POST" });
      const res = await resp.json();
      const report = res.data;

      preflightSpinner.style.display = "none";
      preflightResultIcon.style.display = "flex";
      preflightDetails.style.display = "flex";

      if (res.success && report && report.is_compliant) {
        preflightResultIcon.className = "status-icon success";
        preflightResultIcon.textContent = "✓";
        preflightTitle.textContent = "All Security Pre-Flight Checks Passed!";
        preflightSubtitle.textContent = `Connected to Zoho (${report.zoho_org_name || "Verified"}) & Google Workspace DWD (${report.google_service_account_email || "Verified"})`;
        btnProceedDiscovery.disabled = false;
      } else {
        preflightResultIcon.className = "status-icon danger";
        preflightResultIcon.textContent = "✗";
        preflightTitle.textContent = "Security Pre-Flight Check Failed";
        preflightSubtitle.textContent = "One or more pre-requisite permissions or credentials failed validation.";
        btnProceedDiscovery.disabled = true;
      }

      // Render check badges
      if (report && report.checks_passed) {
        report.checks_passed.forEach(msg => {
          preflightDetails.innerHTML += `
            <div class="diagnostic-item passed">
              <span>✓</span>
              <span>${msg}</span>
            </div>
          `;
        });
      }

      if (report && report.warnings && report.warnings.length > 0) {
        report.warnings.forEach(w => {
          preflightDetails.innerHTML += `
            <div class="diagnostic-item warning">
              <span>⚠️</span>
              <span>${w}</span>
            </div>
          `;
        });
      }

      if (report && report.errors && report.errors.length > 0) {
        report.errors.forEach(e => {
          preflightDetails.innerHTML += `
            <div class="diagnostic-item error">
              <span>✗</span>
              <span>${e}</span>
            </div>
          `;
        });
      }

    } catch (err) {
      preflightSpinner.style.display = "none";
      preflightResultIcon.style.display = "flex";
      preflightResultIcon.className = "status-icon danger";
      preflightResultIcon.textContent = "✗";
      preflightTitle.textContent = "Connection Error";
      preflightSubtitle.textContent = err.message;
    }
  }

  btnRetestPreflight.addEventListener("click", runPreflightCheck);
  btnProceedDiscovery.addEventListener("click", () => {
    navigateToStep(3);
    runDiscoveryAssessment();
  });

  // --- Step 3: Discovery & Volume Assessment ---
  async function runDiscoveryAssessment() {
    discoveryLoading.style.display = "block";
    discoveryDashboard.style.display = "none";
    btnProceedScope.disabled = true;

    try {
      const resp = await fetch("/api/discover", { method: "POST" });
      const res = await resp.json();

      if (!res.success) {
        showAlert("Discovery failed: " + res.error);
        return;
      }

      const report = res.data.report;
      discoveredUsers = report.users || [];
      recommendedPilotEmails = res.data.recommended_pilot_cohort || [];

      // Update Metric Cards
      metricUsers.textContent = report.total_users || discoveredUsers.length || 0;
      const totalMsgs = report.total_estimated_messages ?? report.total_messages ?? 0;
      metricMessages.textContent = totalMsgs.toLocaleString();

      let mbSize = report.total_estimated_storage_mb;
      if (mbSize === undefined && report.total_bytes !== undefined) {
        mbSize = report.total_bytes / (1024 * 1024);
      }
      mbSize = mbSize || 0;
      metricSize.textContent = mbSize >= 1024 ? `${(mbSize/1024).toFixed(2)} GB` : `${mbSize.toFixed(1)} MB`;

      const totalCal = report.total_calendar_events ?? discoveredUsers.reduce((acc, u) => acc + (u.calendar_events_count || 0), 0);
      const totalCont = report.total_contacts ?? discoveredUsers.reduce((acc, u) => acc + (u.contacts_count || 0), 0);
      metricItems.textContent = (totalCal + totalCont).toLocaleString();

      if (recommendedPilotEmails.length > 0) {
        pilotRecommendationText.innerHTML = `
          Recommended safe pilot candidates: <strong>${recommendedPilotEmails.slice(0, 3).join(", ")}</strong>
          (Low storage volume, minimal attachments, non-admin role).
        `;
      } else {
        pilotRecommendationText.textContent = "All discovered users are available for migration selection.";
      }

      discoveryLoading.style.display = "none";
      discoveryDashboard.style.display = "block";
      btnProceedScope.disabled = false;

    } catch (err) {
      showAlert("Error during discovery: " + err.message);
    }
  }

  btnProceedScope.addEventListener("click", () => {
    navigateToStep(4);
    renderUserTable();
    applyPreset("pilot-1");
  });

  // --- Step 4: Pilot & Scope Selection ---
  function renderUserTable(filterQuery = "") {
    userTableBody.innerHTML = "";
    const q = filterQuery.toLowerCase().trim();

    discoveredUsers.forEach((u, idx) => {
      const fullName = `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email.split("@")[0];
      const match = !q || fullName.toLowerCase().includes(q) || u.email.toLowerCase().includes(q);
      if (!match) return;

      const isChecked = selectedUserEmails.has(u.email);
      const isPilot = recommendedPilotEmails.includes(u.email);
      const mb = (u.mailbox_size_bytes / (1024 * 1024)).toFixed(1);

      const row = document.createElement("tr");
      row.innerHTML = `
        <td><input type="checkbox" class="user-row-chk" data-email="${u.email}" ${isChecked ? "checked" : ""}></td>
        <td><strong>${fullName}</strong></td>
        <td><code>${u.email}</code></td>
        <td>${(u.total_messages || 0).toLocaleString()}</td>
        <td>${mb} MB</td>
        <td>${isPilot ? '<span class="badge badge-secure">⭐ Pilot Pick</span>' : '<span class="badge badge-neutral">Standard</span>'}</td>
      `;
      userTableBody.appendChild(row);
    });

    // Attach row checkbox listeners
    document.querySelectorAll(".user-row-chk").forEach(chk => {
      chk.addEventListener("change", (e) => {
        const email = e.target.getAttribute("data-email");
        if (e.target.checked) selectedUserEmails.add(email);
        else selectedUserEmails.delete(email);
        updateSelectionSummary();
      });
    });

    updateSelectionSummary();
  }

  function updateSelectionSummary() {
    const count = selectedUserEmails.size;
    selectionCountBadge.textContent = `${count} User${count === 1 ? "" : "s"} Selected`;
    btnStartMigration.disabled = count === 0;
  }

  function applyPreset(mode) {
    presetButtons.forEach(b => b.classList.remove("active"));
    const btn = document.querySelector(`.btn-preset[data-mode="${mode}"]`);
    if (btn) btn.classList.add("active");

    selectedUserEmails.clear();

    if (mode === "pilot-1") {
      const first = recommendedPilotEmails[0] || (discoveredUsers[0] ? discoveredUsers[0].email : null);
      if (first) selectedUserEmails.add(first);
    } else if (mode === "pilot-5") {
      const picks = recommendedPilotEmails.length > 0 ? recommendedPilotEmails.slice(0, 5) : discoveredUsers.slice(0, 5).map(u => u.email);
      picks.forEach(e => selectedUserEmails.add(e));
    } else if (mode === "all") {
      discoveredUsers.forEach(u => selectedUserEmails.add(u.email));
    }

    renderUserTable(userTableSearch.value);
  }

  presetButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-mode");
      applyPreset(mode);
    });
  });

  userTableSearch.addEventListener("input", (e) => {
    renderUserTable(e.target.value);
  });

  btnSelectAll.addEventListener("click", () => {
    discoveredUsers.forEach(u => selectedUserEmails.add(u.email));
    renderUserTable(userTableSearch.value);
  });

  btnDeselectAll.addEventListener("click", () => {
    selectedUserEmails.clear();
    renderUserTable(userTableSearch.value);
  });

  thSelectAll.addEventListener("change", (e) => {
    if (e.target.checked) {
      discoveredUsers.forEach(u => selectedUserEmails.add(u.email));
    } else {
      selectedUserEmails.clear();
    }
    renderUserTable(userTableSearch.value);
  });

  // --- Step 5: Start Migration & Stream Progress ---
  btnStartMigration.addEventListener("click", async () => {
    const count = selectedUserEmails.size;
    const isDryRun = chkDryRun.checked;

    const confirmMsg = isDryRun
      ? `Simulate migration (Dry-Run) for ${count} selected user(s)?`
      : `⚠️ LIVE MIGRATION: Start actual migration to Google Workspace for ${count} user(s)?`;

    if (!confirm(confirmMsg)) return;

    navigateToStep(5);
    initMigrationStream(isDryRun, Array.from(selectedUserEmails));
  });

  async function initMigrationStream(dryRun, targetEmails) {
    // Reset UI
    progressPctValue.textContent = "0";
    progressBarFill.style.width = "0%";
    progressBarFill.classList.remove("paused");
    activeStagePill.textContent = "Initializing...";
    activeStagePill.className = "badge badge-primary";
    pausedStatePill.style.display = "none";
    progressDetailText.textContent = "Contacting supervisor and starting atomic agents...";
    statActiveUser.textContent = "-";
    statItemsCount.textContent = "0 / 0";
    logTerminal.innerHTML = `<div class="log-line text-muted">[System] Initiating migration pipeline (Dry Run: ${dryRun})...</div>`;
    completionSummaryCard.style.display = "none";
    btnNewMigration.style.display = "none";
    networkWarningBanner.style.display = "none";
    pauseNoticeBanner.style.display = "none";
    btnPauseMigration.style.display = "inline-flex";
    btnPauseMigration.disabled = false;
    btnResumeMigration.style.display = "none";
    btnResumeMigration.disabled = false;
    btnCancelMigration.style.display = "inline-flex";
    btnCancelMigration.disabled = false;

    try {
      const chkMail = document.getElementById("chk-sync-mail");
      const chkCal = document.getElementById("chk-sync-calendar");
      const chkCont = document.getElementById("chk-sync-contacts");

      const resp = await fetch("/api/migrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dry_run: dryRun,
          target_emails: targetEmails,
          skip_mailbox: chkMail ? !chkMail.checked : false,
          skip_calendar: chkCal ? !chkCal.checked : false,
          skip_contacts: chkCont ? !chkCont.checked : false
        })
      });
      const data = await resp.json();
      if (!data.success) {
        showAlert("Failed to start migration: " + data.error);
        return;
      }

      startListeningProgress();
    } catch (err) {
      showAlert("Error initiating migration: " + err.message);
    }
  }

  // --- Pause / Resume / Cancel Action Listeners ---
  btnPauseMigration.addEventListener("click", async () => {
    btnPauseMigration.disabled = true;
    try {
      const resp = await fetch("/api/pause", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Manual pause requested by administrator" })
      });
      const data = await resp.json();
      if (!data.success) {
        showAlert("Failed to pause migration: " + (data.error || "Unknown error"));
      }
    } catch (err) {
      showAlert("Error pausing migration: " + err.message);
    } finally {
      btnPauseMigration.disabled = false;
    }
  });

  btnResumeMigration.addEventListener("click", async () => {
    btnResumeMigration.disabled = true;
    try {
      const resp = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const data = await resp.json();
      if (!data.success) {
        showAlert("Failed to resume migration: " + (data.error || "Unknown error"));
      }
    } catch (err) {
      showAlert("Error resuming migration: " + err.message);
    } finally {
      btnResumeMigration.disabled = false;
    }
  });

  btnCancelMigration.addEventListener("click", async () => {
    if (!confirm("Are you sure you want to CANCEL the migration? Completed items are preserved in checkpoint store.")) return;
    btnCancelMigration.disabled = true;
    try {
      const resp = await fetch("/api/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const data = await resp.json();
      if (!data.success) {
        showAlert("Failed to cancel migration: " + (data.error || "Unknown error"));
      }
    } catch (err) {
      showAlert("Error cancelling migration: " + err.message);
    } finally {
      btnCancelMigration.disabled = false;
    }
  });

  function startListeningProgress() {
    if (eventSource) eventSource.close();
    if (pollInterval) clearInterval(pollInterval);

    try {
      eventSource = new EventSource("/api/progress/stream");
      eventSource.onmessage = (event) => {
        try {
          const state = JSON.parse(event.data);
          handleProgressUpdate(state);
        } catch (e) {}
      };
      eventSource.onerror = () => {
        eventSource.close();
        // Fallback to polling
        pollInterval = setInterval(pollProgress, 1000);
      };
    } catch (e) {
      pollInterval = setInterval(pollProgress, 1000);
    }
  }

  async function pollProgress() {
    try {
      const resp = await fetch("/api/progress/poll");
      const data = await resp.json();
      if (data.success && data.data) {
        handleProgressUpdate(data.data);
      }
    } catch (e) {}
  }

  function handleProgressUpdate(state) {
    if (!state) return;

    // Update Progress
    const pct = state.percent || 0;
    progressPctValue.textContent = pct;
    progressBarFill.style.width = `${pct}%`;

    if (state.stage_name) activeStagePill.textContent = state.stage_name;
    if (state.detail) progressDetailText.textContent = state.detail;
    if (state.current_user) statActiveUser.textContent = state.current_user;
    if (state.item_total !== undefined) statItemsCount.textContent = `${state.item_current} / ${state.item_total}`;

    // Handle Paused & Network States
    const isPaused = Boolean(state.is_paused);
    const pauseState = state.pause_state || "RUNNING";
    const networkOnline = state.network_online !== false;

    if (isPaused) {
      pausedStatePill.style.display = "inline-flex";
      progressBarFill.classList.add("paused");
      btnPauseMigration.style.display = "none";
      btnResumeMigration.style.display = "inline-flex";

      if (pauseState === "PAUSED_NETWORK_LOST" || !networkOnline) {
        networkWarningBanner.style.display = "flex";
        pauseNoticeBanner.style.display = "none";
      } else {
        networkWarningBanner.style.display = "none";
        pauseNoticeBanner.style.display = "flex";
      }
    } else {
      pausedStatePill.style.display = "none";
      progressBarFill.classList.remove("paused");
      btnPauseMigration.style.display = "inline-flex";
      btnResumeMigration.style.display = "none";
      networkWarningBanner.style.display = "none";
      pauseNoticeBanner.style.display = "none";
    }

    // Update Stage Indicators
    updateStageIndicators(state.stage);

    // Update Logs
    if (state.log_messages && state.log_messages.length > 0) {
      logTerminal.innerHTML = state.log_messages.map(m => `<div class="log-line">${m}</div>`).join("");
      logTerminal.scrollTop = logTerminal.scrollHeight;
    }

    // Handle Completion
    if (state.is_completed) {
      if (eventSource) eventSource.close();
      if (pollInterval) clearInterval(pollInterval);

      completionSummaryCard.style.display = "flex";
      btnNewMigration.style.display = "inline-block";
      document.getElementById("migration-status-heading").textContent = "Step 5: Migration Completed!";
      activeStagePill.className = "badge badge-secure";
      activeStagePill.textContent = "Completed Successfully";
      pausedStatePill.style.display = "none";
      btnPauseMigration.style.display = "none";
      btnResumeMigration.style.display = "none";
      btnCancelMigration.style.display = "none";
    }

    // Handle Error
    if (state.error) {
      if (eventSource) eventSource.close();
      if (pollInterval) clearInterval(pollInterval);
      activeStagePill.className = "badge badge-neutral";
      activeStagePill.textContent = "Failed";
      showAlert("Migration halted with error: " + state.error);
      btnPauseMigration.style.display = "none";
      btnResumeMigration.style.display = "none";
      btnCancelMigration.style.display = "none";
    }
  }

  function updateStageIndicators(stage) {
    const stages = ["PROVISIONING", "CALENDAR", "CONTACTS", "MAILBOX"];
    const stageMap = {
      "PROVISIONING": "stage-ind-prov",
      "CALENDAR": "stage-ind-cal",
      "CONTACTS": "stage-ind-cont",
      "MAILBOX": "stage-ind-mail"
    };

    const currentIdx = stages.indexOf(stage);

    stages.forEach((stg, idx) => {
      const el = document.getElementById(stageMap[stg]);
      if (!el) return;
      el.classList.remove("active", "completed");
      if (stage === "COMPLETED" || (currentIdx > idx)) {
        el.classList.add("completed");
      } else if (stage === stg) {
        el.classList.add("active");
      }
    });
  }

  btnNewMigration.addEventListener("click", () => {
    navigateToStep(4);
  });

  // Reset Session
  btnResetSession.addEventListener("click", async () => {
    if (!confirm("Are you sure you want to clear session credentials and reset the UI?")) return;
    try {
      await fetch("/api/reset", { method: "POST" });
      saJsonContent = "";
      saFileName = "";
      discoveredUsers = [];
      selectedUserEmails.clear();
      formCredentials.reset();
      dropzoneText.style.display = "block";
      saFilenameDisplay.style.display = "none";
      navigateToStep(1);
    } catch (e) {
      location.reload();
    }
  });

});
