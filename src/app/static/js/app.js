// --- 토스트 -------------------------------------------------------------------
// 서버는 HX-Trigger: {"toast": "저장했습니다"} 로 띄운다. JS에서는 window.toast("...")
function toast(message, kind = "") {
  let box = document.querySelector(".toasts");
  if (!box) {
    box = document.createElement("div");
    box.className = "toasts";
    box.setAttribute("role", "status");
    document.body.append(box);
  }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  box.append(el);
  setTimeout(() => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 200);
  }, 2400);
}
window.toast = toast;

document.body.addEventListener("toast", (e) => toast(e.detail.value));

// 서버 오류(403, 404, 500 등). htmx는 기본적으로 4xx/5xx 응답을 화면에 넣지 않는다.
document.body.addEventListener("htmx:responseError", (e) => {
  let msg = "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.";
  try {
    const detail = JSON.parse(e.detail.xhr.response).detail;
    if (typeof detail === "string") msg = detail;
  } catch (_) {}
  toast(msg, "error");
});
document.body.addEventListener("htmx:sendError", () => toast("서버에 연결할 수 없어요.", "error"));

// --- 모달 ------------------------------------------------------------------------
// 서버가 <dialog class="modal">을 #modal에 넣으면 showModal()로 연다(배경 어둡게, Esc 닫기).
// 저장 성공 시 서버가 빈 응답을 주면 dialog가 DOM에서 빠지면서 닫힌다.
htmx.onLoad((root) => {
  const sel = "dialog.modal:not([data-manual])"; // data-manual: 직접 여닫는 대화상자(모바일 메뉴)
  const dialogs = root.matches?.(sel) ? [root] : root.querySelectorAll?.(sel) ?? [];
  for (const d of dialogs) {
    if (!d.open) d.showModal();
    d.addEventListener("close", () => d.remove());
    d.addEventListener("click", (e) => {
      if (e.target === d) d.close(); // 배경 클릭
    });
  }
});

// --- 확인 대화상자 (hx-confirm을 브라우저 기본 confirm 대신 표시) -------------------------
function confirmDialog(question, { okText = "확인", danger = false } = {}) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "modal confirm";
    d.innerHTML = `
      <div class="modal-body"></div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" value="no">취소</button>
        <button type="button" class="btn ${danger ? "btn-danger" : ""}" value="yes"></button>
      </div>`;
    d.querySelector(".modal-body").textContent = question;
    d.querySelector('[value="yes"]').textContent = okText;
    let answer = false;
    d.addEventListener("click", (e) => {
      if (e.target.value === "yes") answer = true;
      if (e.target.value || e.target === d) d.close();
    });
    d.addEventListener("close", () => {
      d.remove();
      resolve(answer);
    });
    document.body.append(d);
    d.showModal();
    d.querySelector('[value="yes"]').focus();
  });
}

document.body.addEventListener("htmx:confirm", (e) => {
  if (!e.detail.question) return;
  e.preventDefault();
  const elt = e.detail.elt;
  confirmDialog(e.detail.question, {
    okText: elt.dataset.confirmOk || "확인",
    danger: elt.hasAttribute("data-confirm-danger"),
  }).then((ok) => ok && e.detail.issueRequest(true));
});
