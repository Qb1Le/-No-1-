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
  sec = Math.max(0, sec | 0);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function fillSelect(sel, items, selectedValue) {
  if (!sel) return;
  sel.innerHTML = "";
  for (const v of (items || [])) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    if (selectedValue != null && String(v) === String(selectedValue)) opt.selected = true;
    sel.appendChild(opt);
  }
}

(function boot() {
  if (!window.PAGE) return;

  if (typeof io === "undefined") {
    alert("Socket.IO client не найден. Проверь подключение socket.io.min.js в base.html");
    return;
  }

  const socket = io({ transports: ["websocket"] });
  socket.on("toast", (p) => showToast(p.type || "secondary", p.text || ""));

  // =========================
  // INDEX (matchmaking)
  // =========================
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

  // =========================
  // MATCH (PvP)
  // =========================
  if (PAGE.kind === "match") {
    const timerEl = qs("timer");
    const titleEl = qs("taskTitle");
    const promptEl = qs("taskPrompt");
    const inputEl = qs("answerInput");
    const btnSubmit = qs("btnSubmit");
    const btnSurrender = qs("btnSurrender");
    const resultEl = qs("result");

    socket.emit("match:join", { match_id: PAGE.matchId });

    socket.on("match:task", (t) => {
      // сервер шлёт topic/difficulty/prompt
      const topic = t.topic || "Задача";
      const diff = t.difficulty ? ` • ${t.difficulty}` : "";
      if (titleEl) titleEl.textContent = topic + diff;
      if (promptEl) promptEl.textContent = t.prompt || "";
    });

    socket.on("match:state", (st) => {
      if (timerEl) timerEl.textContent = fmtTime(st.seconds_left);
    });

    socket.on("match:started", (p) => {
      if (timerEl) timerEl.textContent = fmtTime(p.seconds_left);
      showToast("primary", "Матч начался!");
    });

    socket.on("match:tick", (p) => {
      if (timerEl) timerEl.textContent = fmtTime(p.seconds_left);
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
    });
  }

  // =========================
  // TRAINING (PvE) + filters
  // =========================
  if (PAGE.kind === "training") {
    const timerEl = qs("timer");
    const titleEl = qs("taskTitle");
    const promptEl = qs("taskPrompt");
    const inputEl = qs("answerInput");
    const btnSubmit = qs("btnSubmit");
    const btnStop = qs("btnStop");
    const resultEl = qs("result");
    const statsEl = qs("stats");

    const selSubject = qs("selSubject");
    const selTopic = qs("selTopic");
    const selDifficulty = qs("selDifficulty");

    let suppressFilterEmit = false;

    // Заголовок БЕЗ предмета (чтобы не было "Информатика ...")
    function setHeader(_subject, topic, diff) {
      if (!titleEl) return;
      const t = topic || "Тренировка";
      const d = diff ? ` • ${diff}` : "";
      titleEl.textContent = t + d;
    }

    function setStats(stats) {
      if (!statsEl) return;
      const solved = stats?.solved ?? 0;
      const total = stats?.total ?? 0;
      statsEl.textContent = `Решено: ${solved} / ${total}`;
    }

    function resetInput() {
      if (inputEl) {
        inputEl.value = "";
        inputEl.disabled = false;
        inputEl.focus();
      }
      if (btnSubmit) btnSubmit.disabled = false;
    }

    function applyFilters() {
      if (suppressFilterEmit) return;
      socket.emit("training:set_filters", {
        subject: selSubject?.value || "Любой",
        topic: selTopic?.value || "Любая",
        difficulty: selDifficulty?.value || "Любая",
      });
    }

    // join
    socket.emit("training:join", {});

    socket.on("training:options", (opt) => {
      suppressFilterEmit = true;

      const subjects = opt?.subjects || ["Любой"];
      const topics = opt?.topics || ["Любая"];
      const diffs = opt?.difficulties || ["Любая", "Легкая", "Средняя", "Сложная"];

      fillSelect(selSubject, subjects, selSubject?.value || "Любой");
      fillSelect(selTopic, topics, selTopic?.value || "Любая");
      fillSelect(selDifficulty, diffs, selDifficulty?.value || "Любая");

      suppressFilterEmit = false;
    });

    socket.on("training:task", (t) => {
      // синхронизируем селекты с сервером (без лишних эмитов)
      const filters = t.filters || null;
      if (filters) {
        suppressFilterEmit = true;
        if (selSubject) selSubject.value = filters.subject || "Любой";
        if (selTopic) selTopic.value = filters.topic || "Любая";
        if (selDifficulty) selDifficulty.value = filters.difficulty || "Любая";
        suppressFilterEmit = false;
      }

      setHeader(t.subject, t.topic, t.difficulty);
      if (promptEl) promptEl.textContent = t.prompt || "";
      if (timerEl) timerEl.textContent = fmtTime(t.seconds_left ?? 0);
      setStats(t.stats);

      if (resultEl) resultEl.innerHTML = "";
      resetInput();
    });

    socket.on("training:tick", (p) => {
      if (timerEl) timerEl.textContent = fmtTime(p.seconds_left ?? 0);
    });

    selSubject?.addEventListener("change", applyFilters);
    selTopic?.addEventListener("change", applyFilters);
    selDifficulty?.addEventListener("change", applyFilters);

    btnSubmit?.addEventListener("click", () => {
      const ans = (inputEl?.value || "").trim();
      if (!ans) {
        showToast("warning", "Введи ответ.");
        return;
      }
      if (btnSubmit) btnSubmit.disabled = true;
      if (inputEl) inputEl.disabled = true;
      socket.emit("training:submit_answer", { answer: ans });
    });

    inputEl?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        btnSubmit?.click();
      }
    });

    btnStop?.addEventListener("click", () => {
      socket.emit("training:leave", {});
      if (btnSubmit) btnSubmit.disabled = true;
      if (inputEl) inputEl.disabled = true;
      showToast("secondary", "Тренировка остановлена");
    });

    socket.on("training:result", (p) => {
      const ok = !!p.correct;
      const reason = p.reason || "answer";
      const correctAnswer = p.correct_answer ?? "—";

      setStats(p.stats);

      let subtitle = "";
      if (reason === "timeout") subtitle = "Время вышло ⏱️";
      else subtitle = ok ? "Верно ✅" : "Неверно ❌";

      if (resultEl) {
        resultEl.innerHTML = `
          <div class="alert ${ok ? "alert-success" : "alert-danger"}">
            <div class="fw-semibold mb-1">${subtitle}</div>
            <div class="small">Правильный ответ: <span class="fw-semibold">${correctAnswer}</span></div>
            <div class="small text-muted mt-1">Следующая задача сейчас появится…</div>
          </div>
        `;
      }
    });
  }
})();
