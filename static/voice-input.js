(() => {
  const button = document.getElementById("voice-input");
  const field = document.getElementById("conversation");
  const status = document.getElementById("voice-status");
  const help = document.getElementById("voice-help");
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    status.textContent = "이 브라우저에서는 음성 입력을 사용할 수 없습니다. 내용을 직접 입력해 주세요.";
    return;
  }

  const errors = {
    "not-allowed": "마이크 권한을 허용하거나 내용을 직접 입력해 주세요.",
    "service-not-allowed": "브라우저에서 음성 인식 서비스를 사용할 수 없습니다.",
    "audio-capture": "마이크 연결과 설정을 확인해 주세요.",
    "no-speech": "음성을 듣지 못했습니다. 다시 시도해 주세요.",
    network: "음성 인식 서비스에 연결하지 못했습니다. 직접 입력할 수 있습니다.",
  };
  let active = null;
  button.hidden = false;
  help.hidden = false;

  button.addEventListener("click", () => {
    if (active) {
      button.disabled = true;
      status.textContent = "음성 입력을 마무리하는 중입니다.";
      active.stop();
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = "ko-KR";
    recognition.continuous = true;
    recognition.interimResults = false;
    let addedText = false;
    let failure = "";

    recognition.onstart = () => {
      button.disabled = false;
      status.textContent = "듣고 있습니다. 말을 마치면 음성 입력 종료를 눌러 주세요.";
    };
    recognition.onresult = (event) => {
      for (let index = event.resultIndex; index < event.results.length; index++) {
        const result = event.results[index];
        if (!result.isFinal) continue;
        const transcript = result[0].transcript.trim();
        if (!transcript) continue;
        const separator = field.value && !/\s$/.test(field.value) ? " " : "";
        const remaining = field.maxLength - field.value.length - separator.length;
        if (remaining > 0) {
          field.value += separator + transcript.slice(0, remaining);
          addedText = true;
        }
        if (transcript.length >= remaining) {
          failure = "입력 한도에 도달했습니다. 변환된 내용을 확인해 주세요.";
          button.disabled = true;
          recognition.stop();
          break;
        }
      }
    };
    recognition.onerror = (event) => {
      failure ||= errors[event.error] || "음성 입력을 완료하지 못했습니다. 직접 입력할 수 있습니다.";
      status.textContent = failure;
    };
    recognition.onend = () => {
      active = null;
      button.disabled = false;
      button.setAttribute("aria-pressed", "false");
      button.textContent = "음성 입력 시작";
      status.textContent = failure || (addedText
        ? "음성을 텍스트로 입력했습니다. 내용을 확인한 뒤 제출해 주세요."
        : "입력된 음성이 없습니다. 다시 시도하거나 직접 입력해 주세요.");
    };
    try {
      active = recognition;
      button.disabled = true;
      button.setAttribute("aria-pressed", "true");
      button.textContent = "음성 입력 종료";
      status.textContent = "마이크를 연결하는 중입니다.";
      recognition.start();
    } catch {
      failure = "음성 입력을 시작하지 못했습니다. 마이크 권한을 확인해 주세요.";
      recognition.onend();
    }
  });

  field.form.addEventListener("submit", (event) => {
    if (active) {
      event.preventDefault();
      status.textContent = "음성 입력을 종료하고 변환된 내용을 확인한 뒤 제출해 주세요.";
    }
  });
  window.addEventListener("pagehide", () => active?.abort());
})();
