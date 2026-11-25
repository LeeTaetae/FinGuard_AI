const statusMessage = document.querySelector("[data-excel-ready]");
const openButton = document.querySelector("#open-excel-draft");

async function waitForExcelDraft(deadline) {
  try {
    const response = await fetch(statusMessage.dataset.excelReady, {
      cache: "no-store",
      redirect: "error",
    });
    if (response.ok) {
      const { url, ticket } = await response.json();
      if (url && ticket) {
        const current = new URL(window.location.href);
        current.searchParams.delete("open_excel");
        window.history.replaceState(null, "", current);
        const form = document.createElement("form");
        form.method = "POST";
        form.action = url;
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "ticket";
        input.value = ticket;
        form.append(input);
        document.body.append(form);
        form.submit();
        return;
      }
    }
  } catch {
    // A temporary connection problem can clear while the job runs.
  }
  if (Date.now() < deadline) {
    window.setTimeout(() => waitForExcelDraft(deadline), 2500);
  } else {
    openButton.disabled = false;
    statusMessage.textContent = "엑셀 폼이 아직 준비되지 않았습니다. Job 상태와 엑셀 접수 사이트의 저장 대기 목록을 확인해 주세요.";
  }
}

function openExcelDraft() {
  openButton.disabled = true;
  statusMessage.textContent = "엑셀 접수 폼을 준비 중입니다.";
  waitForExcelDraft(Date.now() + 120_000);
}

openButton.addEventListener("click", openExcelDraft);
if (statusMessage.hasAttribute("data-auto-open")) {
  openExcelDraft();
}
