/* Service drafts belong to the signed-in operator, never the tablet enrollee. */
(() => {
  "use strict";
  const page = document.getElementById("aircraft-service-page");
  if (!page) return;
  const key = `r2c-service:${page.dataset.organization}:${page.dataset.actor}`;
  const status = document.getElementById("service-upload-status");
  const list = document.getElementById("service-pending-reports");
  let queue;
  try { queue = JSON.parse(localStorage.getItem(key) || "[]"); }
  catch (_) { queue = []; }
  if (!Array.isArray(queue)) queue = [];
  queue = queue.filter(item => item && item.fields && typeof item.fields.event_id === "string" && typeof item.fields.remote_id === "string");
  let sending = false;
  function persist() { localStorage.setItem(key, JSON.stringify(queue)); }
  function render() {
    list.replaceChildren();
    for (const report of queue) {
      const item = document.createElement("p");
      item.textContent = `${report.conflict ? "Review required" : "Pending upload"}: ${report.fields.remote_id} — ${report.fields.note}. `;
      if (report.conflict) {
        const review = document.createElement("button");
        review.type = "button";
        review.textContent = "Load draft for review";
        review.onclick = () => {
          const form = Array.from(document.querySelectorAll(".aircraft-service-form")).find(f => f.elements.remote_id.value === report.fields.remote_id);
          if (!form) { status.textContent = "Aircraft configuration changed. Reload and review this retained draft."; return; }
          for (const name of ["note", "status", "self_remediation"]) form.elements[name].value = report.fields[name] || "";
          form.closest("details").open = true;
          form.elements.event_id.value = crypto.randomUUID();
          form.elements.note.focus();
          status.textContent = "Review the latest aircraft status before submitting a new report. The conflicted draft remains below until you discard it.";
        };
        item.append(review);
      }
      const discard = document.createElement("button");
      discard.type = "button"; discard.textContent = "Discard local draft";
      discard.onclick = () => {
        if (sending) { status.textContent = "Upload is in progress. Wait for its result before discarding a local draft."; return; }
        queue = queue.filter(entry => entry !== report); persist();
        for (const form of document.querySelectorAll(".aircraft-service-form")) {
          if (form.elements.event_id.value === report.fields.event_id) form.elements.event_id.value = crypto.randomUUID();
        }
        render();
      };
      item.append(discard); list.append(item);
    }
  }
  async function flush() {
    if (sending || !navigator.onLine) return;
    sending = true;
    try {
      for (const report of [...queue]) {
        if (report.conflict) continue;
        const fields = { ...report.fields, actor_id: page.dataset.actor, form_token: page.dataset.token };
        let response;
        try {
          response = await fetch(`/${page.dataset.organization}/aircraft/service`, {
            method: "POST", credentials: "same-origin", redirect: "error",
            headers: { "X-R2C-Response": "json" }, body: new URLSearchParams(fields)
          });
        } catch (_) { status.textContent = "Pending upload — report retained on this browser. Reopen this page when connected."; break; }
        if (response.ok) {
          const event = await response.json();
          queue = queue.filter(entry => entry !== report); persist();
          for (const form of document.querySelectorAll(".aircraft-service-form")) {
            if (form.elements.remote_id.value === event.remoteId) {
              form.elements.revision.value = String(event.revision);
              form.elements.event_id.value = crypto.randomUUID();
            }
          }
          status.textContent = "Report received by Tracker. " + (event.notificationsQueued ? "Equipment-manager notification is queued. " : "No equipment manager was assigned for email. ") + "Reload to view the updated history.";
        } else if ([400, 404, 409, 422].includes(response.status)) {
          report.conflict = true; persist();
          status.textContent = "Report retained for review. Reload for the latest status; an older report will not overwrite a newer one.";
        } else {
          status.textContent = "Pending upload — sign in as the same operator and reopen this page to retry.";
          break;
        }
      }
    } finally { sending = false; render(); }
  }
  for (const form of document.querySelectorAll(".aircraft-service-form")) {
    form.addEventListener("submit", event => {
      if (!form.reportValidity()) return;
      event.preventDefault();
      const fields = Object.fromEntries(new FormData(form));
      delete fields.form_token;
      fields.reported_at = new Date().toISOString();
      if (fields.status === "out_of_service" && !fields.self_remediation) {
        status.textContent = "Specify whether you will address the problem yourself."; return;
      }
      if (queue.some(item => item.fields.event_id === fields.event_id)) {
        status.textContent = "This report is already pending. Its saved note is shown below. Wait for upload or discard the local draft before submitting changes."; return;
      }
      queue.push({ fields });
      try { persist(); }
      catch (_) { queue = queue.filter(item => item.fields.event_id !== fields.event_id); if (navigator.onLine) { form.submit(); return; } status.textContent = "This browser could not save a pending report. Keep this page open and submit while connected."; return; }
      status.textContent = "Pending upload — saved on this browser, not yet received by Tracker.";
      render(); void flush();
    });
  }
  window.addEventListener("online", flush);
  setInterval(flush, 30000);
  render(); void flush();
})();
