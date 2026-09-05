(function () {
  "use strict";

  const state = document.getElementById("streams-live-status");
  if (!state) return;

  const designator = state.dataset.designator || "";
  const deviceId = state.dataset.deviceId || "";
  const streamFilter = state.dataset.streamFilter || "";
  const sessionFilter = state.dataset.sessionFilter || "";
  // Preflight and media controllers own their request lifecycle. Reloading an
  // active controller because the tablet's advertised stream set changes can
  // destroy an otherwise healthy WebRTC session (for example, during a brief
  // decoder/UI transition on the tablet).
  function requestControllerActive() {
    if (document.getElementById("video-preflight")) return true;
    const mediaController = document.getElementById("video-media");
    return Boolean(
      mediaController && mediaController.dataset.controllerActive !== "false"
    );
  }
  const renderedMembershipRevision = state.dataset.membershipRevision || "";
  let renderedInProgressSessionIds = [];
  try {
    renderedInProgressSessionIds = JSON.parse(
      state.dataset.inProgressSessionIds || "[]"
    ).map(String).sort();
  } catch (_error) {
    renderedInProgressSessionIds = [];
  }
  const query = new URLSearchParams();
  if (deviceId) query.set("device", deviceId);
  if (streamFilter) query.set("stream", streamFilter);
  if (sessionFilter) query.set("session", sessionFilter);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const statusUrl = `${state.dataset.statusUrl || ""}${suffix}`;
  let watchActive = state.dataset.watchActive === "true";
  let timer = null;
  let stopped = false;
  let refreshPromise = null;
  let refreshQueued = false;
  let windowFocused = document.hasFocus();
  const activeRefreshMs = 10000;

  function pageHasFocus() {
    return !document.hidden && windowFocused;
  }

  function suspend() {
    window.clearTimeout(timer);
    timer = null;
  }

  function stopForNavigation() {
    stopped = true;
    suspend();
  }

  document.addEventListener("submit", stopForNavigation, true);

  function reloadForMembershipChange() {
    if (stopped) return;
    stopped = true;
    suspend();
    window.location.reload();
  }

  function previewImage(sessionId) {
    return Array.from(document.querySelectorAll(".stream-preview-image"))
      .find((image) => image.dataset.streamSessionId === sessionId);
  }

  function updatePreview(item) {
    const image = previewImage(item.sessionId);
    if (!image || !item.thumbnailUrl) return;
    if (image.dataset.thumbnailRevision === item.thumbnailRevision) return;

    image.classList.add("is-refreshing");
    const replacement = new Image();
    replacement.onload = function () {
      image.src = item.thumbnailUrl;
      image.dataset.thumbnailRevision = item.thumbnailRevision;
      image.hidden = false;
      const previewCell = image.closest(".stream-preview-cell");
      const pending = previewCell &&
        previewCell.querySelector(".stream-preview-pending");
      const label = previewCell &&
        previewCell.querySelector(".stream-preview-label");
      if (pending) pending.hidden = true;
      if (label) label.hidden = false;
      image.classList.remove("is-refreshing");
    };
    replacement.onerror = function () {
      image.classList.remove("is-refreshing");
    };
    replacement.src = item.thumbnailUrl;
  }

  async function fetchAndReconcile() {
    if (stopped || !statusUrl) return;
    const response = await fetch(statusUrl, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`Stream status ${response.status}`);
    const status = await response.json();
    window.dispatchEvent(new CustomEvent("r2c:streams-changed", {
      detail: status,
    }));
    if (!requestControllerActive() &&
        status.membershipRevision !== renderedMembershipRevision) {
      reloadForMembershipChange();
      return;
    }
    const currentInProgressSessionIds = (status.inProgressSessionIds || [])
      .map(String)
      .sort();
    if (!requestControllerActive() &&
        JSON.stringify(currentInProgressSessionIds) !==
        JSON.stringify(renderedInProgressSessionIds)) {
      reloadForMembershipChange();
      return;
    }
    watchActive = (status.streams || []).length > 0 ||
      currentInProgressSessionIds.length > 0;
    (status.streams || []).forEach(updatePreview);
  }

  function scheduleRefresh() {
    suspend();
    if (stopped || !watchActive || !pageHasFocus()) return;
    timer = window.setTimeout(reconcile, activeRefreshMs);
  }

  function reconcile() {
    if (refreshPromise) {
      refreshQueued = true;
      return refreshPromise;
    }
    refreshPromise = fetchAndReconcile()
      .catch(function () {
        // A still-active page gets another bounded status request; an idle
        // page waits for the operator to focus it again.
      })
      .finally(function () {
        refreshPromise = null;
        if (refreshQueued && !stopped) {
          refreshQueued = false;
          reconcile();
          return;
        }
        scheduleRefresh();
      });
    return refreshPromise;
  }

  function syncPageActivity() {
    if (!pageHasFocus()) {
      suspend();
      return;
    }
    // A newly focused page performs one bounded reconciliation even when it
    // was rendered idle. Repeated checks run only while the server reports
    // an advertised stream or an in-progress request.
    if (!stopped) reconcile();
  }

  function handleFocus() {
    windowFocused = true;
    syncPageActivity();
  }

  function handleBlur() {
    windowFocused = false;
    syncPageActivity();
  }

  document.addEventListener("visibilitychange", syncPageActivity);
  window.addEventListener("focus", handleFocus);
  window.addEventListener("blur", handleBlur);
  window.addEventListener("pageshow", syncPageActivity);
  reconcile();
})();
