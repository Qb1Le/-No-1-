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
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

(function boot(){
  if (!window.PAGE) return;

  const socket = io({ transports: ["websocket"] });

  socket.on("toast", (p) => showToast(p.type || "secondary", p.text || ""));

  if (PAGE.kind === "lobby") {
    // роль определяем грубо: если имя == host -> host, иначе guest
    const role = (PAGE.name === PAGE.hostName) ? "host" : "guest";

    socket.emit("lobby:join", { lobby_id: PAGE.lobbyId, name: PAGE.name, role });

    socket.on("lobby:state", (st) => {
      qs("statusBadge").textContent = st.status;
      const guest = st.guest_name || "ожидание...";
      qs("guestName").textContent = guest;

      const canGo = (st.status === "full" || st.status === "started");
      const btn = qs("goMatch");
      if (canGo) btn.classList.remove("disabled");
      else btn.classList.add("disabled");
    });

    socket.on("lobby:ready", () => {
      showToast("success", "Соперник найден! Можно переходить в матч.");
      const btn = qs("goMatch");
      btn.classList.remove("disabled");
    });
  }

  if (PAGE.kind === "match") {
    const timerEl = qs("timer");
    const logEl = qs("log");
    const resultEl = qs("result");

    function log(line) {
      if (!logEl) return;
      logEl.textContent = (logEl.textContent ? logEl.textContent + "\n" : "") + line;
    }

    socket.emit("match:join", { lobby_id: PAGE.lobbyId, name: PAGE.name });

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
      const winner = p.winner;
      const reason = p.reason;
      if (winner) {
        resultEl.innerHTML = `<div class="alert alert-success">
          Победитель: <span class="fw-semibold">${winner}</span> · причина: ${reason}
        </div>`;
      } else {
        resultEl.innerHTML = `<div class="alert alert-warning">
          Матч завершён: ничья/время · причина: ${reason}
        </div>`;
      }
      showToast("secondary", "Матч завершён");
      log(`ended: winner=${winner || "none"} reason=${reason}`);
      // блокируем кнопки
      const a = qs("btnSolved"); if (a) a.disabled = true;
      const b = qs("btnSurrender"); if (b) b.disabled = true;
    });

    const btnSolved = qs("btnSolved");
    if (btnSolved) {
      btnSolved.addEventListener("click", () => {
        socket.emit("match:finish", {
          lobby_id: PAGE.lobbyId,
          name: PAGE.name,
          winner: PAGE.name,
          reason: "solve"
        });
      });
    }

    const btnSurrender = qs("btnSurrender");
    if (btnSurrender) {
      btnSurrender.addEventListener("click", () => {
        // победитель — “другой” на сервере мы не вычисляем, поэтому отправим winner пустым
        socket.emit("match:finish", {
          lobby_id: PAGE.lobbyId,
          name: PAGE.name,
          winner: "",
          reason: "surrender"
        });
      });
    }
  }
})();
