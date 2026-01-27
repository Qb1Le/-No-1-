function qs(id) { return document.getElementById(id); }

function showToast(type, text) {
  const host = qs("toastHost");
  if (!host) return;

  const el = document.createElement("div");
  el.className = `toast align-items-center text-bg-${type} border-0`;
  el.role = "alert";
  el.ariaLive = "assertive";
  el.ariaAtomic = "true";
  el.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${text}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>
  `;
  host.appendChild(el);

  const t = new bootstrap.Toast(el, { delay: 2500 });
  t.show();
  el.addEventListener("hidden.bs.toast", () => el.remove());
}

function fmtTime(sec) {
  sec = Math.max(0, sec|0);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

(function boot() {
  if (!window.PAGE) return;

  const socket = io({ transports: ["websocket"] });

  socket.on("toast", (p) => showToast(p.type || "secondary", p.text || ""));

  // -------------------
  // INDEX: matchmaking
  // -------------------
  if (PAGE.kind === "index") {
    const btnFind = qs("btnFind");
    const btnCancel = qs("btnCancel");
    const statusText = qs("statusText");
    const statusBox = qs("statusBox");

    function setStatus(s) {
      statusText.textContent = s;
      statusBox.classList.remove("alert-secondary", "alert-primary", "alert-success", "alert-warning");
      if (s === "searching") statusBox.classList.add("alert-primary");
      else if (s === "found") statusBox.classList.add("alert-success");
      else statusBox.classList.add("alert-secondary");
    }

    btnFind?.addEventListener("click", () => {
      socket.emit("queue:join", {});
      btnFind.disabled = true;
      btnCancel.disabled = false;
      setStatus("searching");
    });

    btnCancel?.addEventListener("click", () => {
      socket.emit("queue:leave", {});
      btnFind.disabled = false;
      btnCancel.disabled = true;
      setStatus("idle");
    });

    socket.on("queue:status", (p) => {
      const st = p.status || "idle";
      setStatus(st);
      if (st === "idle") {
        btnFind.disabled = false;
        btnCancel.disabled = true;
      }
    });

    socket.on("match:found", (p) => {
      setStatus("found");
      showToast("success", `Матч найден! Противник: ${p.opponent_name} (${p.opponent_rating})`);
      const matchId = p.match_id;
      // Перейти на страницу матча
      window.location.href = `/match/${matchId}`;
    });
  }

  // -------------------
  // MATCH: realtime
  // -------------------
  if (PAGE.kind === "match") {
    const timerEl = qs("timer");
    const logEl = qs("log");
    const resultEl = qs("result");

    function log(line) {
      if (!logEl) return;
      logEl.textContent = (logEl.textContent ? logEl.textContent + "\n" : "") + line;
    }

    socket.emit("match:join", { match_id: PAGE.matchId });

    socket.on("match:state", (st) => {
      timerEl.textContent = fmtTime(st.seconds_left);
      log(`state: running=${st.running} left=${st.seconds_left}s`);
    });

    socket.on("match:started", (p) => {
      timerEl.textContent = fmtTime(p.seconds_left);
      showToast("primary", "Матч начался!");
      log("match started");
    });

    socket.on("match:tick", (p) => {
      timerEl.textContent = fmtTime(p.seconds_left);
    });

    socket.on("match:ended", (p) => {
      const winnerId = p.winner_user_id;
      const reason = p.reason;

      if (winnerId === null || winnerId === undefined) {
        resultEl.innerHTML = `<div class="alert alert-warning">Матч завершён: время/ничья · причина: ${reason}</div>`;
      } else {
        const winnerName = (winnerId === p.p1_id) ? p.p1_name : p.p2_name;
        resultEl.innerHTML = `<div class="alert alert-success">Победитель: <span class="fw-semibold">${winnerName}</span> · причина: ${reason}</div>`;
      }

      showToast("secondary", "Матч завершён");
      log(`ended: winner_user_id=${winnerId} reason=${reason}`);

      const a = qs("btnSolved"); if (a) a.disabled = true;
      const b = qs("btnSurrender"); if (b) b.disabled = true;
    });

    qs("btnSolved")?.addEventListener("click", () => {
      socket.emit("match:finish", { match_id: PAGE.matchId, reason: "solve" });
    });

    qs("btnSurrender")?.addEventListener("click", () => {
      socket.emit("match:finish", { match_id: PAGE.matchId, reason: "surrender" });
    });
  }
})();
