function qs(id) { return document.getElementById(id); }

function showToast(type, text) {
  try {
    const host = qs("toastHost");
    if (!host || !window.bootstrap || !bootstrap.Toast) {
      if (type === "danger" || type === "warning") alert(text);
      return;
    }
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
  } catch (_) {}
}

function fmtTime(sec) {
  sec = Math.max(0, sec|0);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

(function boot() {
  if (!window.PAGE) return;
  if (typeof io === "undefined") {
    alert("Socket.IO client не найден. Проверь подключение socket.io.min.js в base.html");
    return;
  }

  const socket = io({ transports: ["websocket"] });

  socket.on("toast", (p) => showToast(p.type || "secondary", p.text || ""));

  // INDEX: matchmaking (как было)
  if (PAGE.kind === "index") {
    const btnFind = qs("btnFind");
    const btnCancel = qs("btnCancel");
    const statusText = qs("statusText");
    const statusBox = qs("statusBox");

    if (!btnFind || !btnCancel || !statusText) return;

    function setStatus(s) {
      statusText.textContent = s;
      if (!statusBox) return;
      statusBox.classList.remove("alert-secondary", "alert-primary", "alert-success");
      if (s === "searching") statusBox.classList.add("alert-primary");
      else if (s === "found") statusBox.classList.add("alert-success");
      else statusBox.classList.add("alert-secondary");
    }

    btnFind.addEventListener("click", () => {
      setStatus("searching");
      btnFind.disabled = true;
      btnCancel.disabled = false;
      socket.emit("queue:join", {});
    });

    btnCancel.addEventListener("click", () => {
      setStatus("idle");
      btnFind.disabled = false;
      btnCancel.disabled = true;
      socket.emit("queue:leave", {});
    });

    socket.on("queue:status", (p) => {
      const st = p?.status || "idle";
      setStatus(st);
      if (st === "idle") {
        btnFind.disabled = false;
        btnCancel.disabled = true;
      }
    });

    socket.on("match:found", (p) => {
      setStatus("found");
      showToast("success", `Матч найден! Противник: ${p.opponent_name} (${p.opponent_rating})`);
      window.location.href = `/match/${p.match_id}`;
    });
  }

  // MATCH: submit answer + results
  if (PAGE.kind === "match") {
    const timerEl = qs("timer");
    const titleEl = qs("taskTitle");
    const promptEl = qs("taskPrompt");
    const inputEl = qs("answerInput");
    const btnSubmit = qs("btnSubmit");
    const btnSurrender = qs("btnSurrender");
    const resultEl = qs("result");
    const logEl = qs("log");

    function log(line) {
      if (!logEl) return;
      logEl.textContent = (logEl.textContent ? logEl.textContent + "\n" : "") + line;
    }

    socket.emit("match:join", { match_id: PAGE.matchId });

    socket.on("match:task", (t) => {
      if (titleEl) titleEl.textContent = t.title || "Задача";
      if (promptEl) promptEl.textContent = t.prompt || "";
    });

    socket.on("match:state", (st) => {
      if (timerEl) timerEl.textContent = fmtTime(st.seconds_left);
      log(`state: running=${st.running} left=${st.seconds_left}s`);
    });

    socket.on("match:started", (p) => {
      if (timerEl) timerEl.textContent = fmtTime(p.seconds_left);
      showToast("primary", "Матч начался!");
      log("match started");
    });

    socket.on("match:tick", (p) => {
      if (timerEl) timerEl.textContent = fmtTime(p.seconds_left);
    });

    socket.on("match:submitted", (p) => {
      // кто-то сдал ответ (без раскрытия)
      log(`submitted user_id=${p.user_id}`);
    });

    btnSubmit?.addEventListener("click", () => {
      const ans = (inputEl?.value || "").trim();
      if (!ans) {
        showToast("warning", "Введи ответ.");
        return;
      }

      socket.emit("match:submit_answer", { match_id: PAGE.matchId, answer: ans });
      showToast("success", "Ответ отправлен. Можно изменить и отправить снова до конца таймера.");
    });

    btnSurrender?.addEventListener("click", () => {
      btnSurrender.disabled = true;
      socket.emit("match:surrender", { match_id: PAGE.matchId });
    });

    socket.on("match:ended", (p) => {
      const winnerId = p.winner_user_id;
      const reason = p.reason;

      const p1ok = p.p1_correct ? "✅" : "❌";
      const p2ok = p.p2_correct ? "✅" : "❌";

      let headline = "";
      if (winnerId === null || typeof winnerId === "undefined") {
        headline = `Ничья`;
      } else {
        const winnerName = (winnerId === p.p1_id) ? p.p1_name : p.p2_name;
        headline = `Победитель: ${winnerName}`;
      }

      if (resultEl) {
        resultEl.innerHTML = `
          <div class="alert alert-secondary">
            <div class="fw-semibold mb-2">${headline}</div>
            <div class="small">Правильный ответ: <span class="fw-semibold">${p.correct_answer}</span></div>
            <hr class="my-2">
            <div class="small">Ответ ${p.p1_name}: <span class="fw-semibold">${p.p1_answer ?? "—"}</span> ${p1ok}</div>
            <div class="small">Ответ ${p.p2_name}: <span class="fw-semibold">${p.p2_answer ?? "—"}</span> ${p2ok}</div>
          </div>
        `;
      }

      if (btnSubmit) btnSubmit.disabled = true;
      if (inputEl) inputEl.disabled = true;
      if (btnSurrender) btnSurrender.disabled = true;

      showToast("secondary", "Матч завершён");
      log(`ended: winner=${winnerId} reason=${reason}`);
    });
  }
})();
