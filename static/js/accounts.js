// ============================================================
// افزودن اکانت کارزار: ارسال کد → دریافت کد از کاربر → ورود
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('modal-add-account');
    if (!modal) return;

    const nameInput = document.getElementById('karzar-name');
    const identInput = document.getElementById('karzar-identifier');
    const otpInput = document.getElementById('karzar-otp');
    const stepIdentity = modal.querySelector('[data-step="identity"]');
    const stepOtp = modal.querySelector('[data-step="otp"]');
    const statusBox = document.getElementById('karzar-status');
    const statusText = document.getElementById('karzar-status-text');
    const spinner = document.getElementById('karzar-spinner');
    const sendBtn = document.getElementById('karzar-send-btn');
    const verifyBtn = document.getElementById('karzar-verify-btn');
    const cancelBtn = document.getElementById('karzar-cancel-btn');

    let sid = null;
    let pollTimer = null;

    const TERMINAL = ['success', 'error', 'timeout', 'cancelled', 'not_found'];

    function setStatus(text, { error = false, busy = true } = {}) {
        statusBox.classList.remove('hidden');
        statusBox.classList.add('flex');
        statusText.textContent = text;
        statusText.classList.toggle('text-red-500', error);
        statusText.classList.toggle('text-slate-600', !error);
        spinner.classList.toggle('hidden', !busy);
    }

    function hideStatus() {
        statusBox.classList.add('hidden');
        statusBox.classList.remove('flex');
    }

    function setBusy(btn, busy) {
        btn.disabled = busy;
        btn.classList.toggle('opacity-60', busy);
        btn.classList.toggle('cursor-not-allowed', busy);
    }

    function showOtpStep() {
        stepIdentity.classList.add('hidden');
        stepOtp.classList.remove('hidden');
        sendBtn.classList.add('hidden');
        verifyBtn.classList.remove('hidden');
        setBusy(verifyBtn, false);
        otpInput.value = '';
        otpInput.focus();
    }

    function reset() {
        stopPolling();
        if (sid) {
            fetch(`/accounts/add/cancel/${sid}`, { method: 'POST' }).catch(() => {});
        }
        sid = null;
        nameInput.value = '';
        identInput.value = '';
        otpInput.value = '';
        stepIdentity.classList.remove('hidden');
        stepOtp.classList.add('hidden');
        sendBtn.classList.remove('hidden');
        verifyBtn.classList.add('hidden');
        setBusy(sendBtn, false);
        setBusy(verifyBtn, false);
        hideStatus();
    }

    function stopPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
    }

    async function postJSON(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) {
            throw new Error(data.error || 'خطای ناشناخته');
        }
        return data;
    }

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(async () => {
            if (!sid) return stopPolling();
            let st;
            try {
                const res = await fetch(`/accounts/add/status/${sid}`);
                st = await res.json();
            } catch (_) {
                return;
            }

            if (st.status === 'waiting_otp') {
                if (stepOtp.classList.contains('hidden')) showOtpStep();
                setStatus(st.message || 'کد را وارد کنید.', { busy: false });
                return;
            }

            if (st.status === 'success') {
                stopPolling();
                setStatus(st.message || 'ورود موفق.', { busy: false });
                setTimeout(() => window.location.reload(), 900);
                return;
            }

            if (TERMINAL.includes(st.status)) {
                stopPolling();
                sid = null;
                setStatus(st.error || 'خطا در ورود.', { error: true, busy: false });
                if (!stepOtp.classList.contains('hidden')) {
                    // اجازهٔ تلاش دوباره از ابتدا
                    stepIdentity.classList.remove('hidden');
                    stepOtp.classList.add('hidden');
                    sendBtn.classList.remove('hidden');
                    verifyBtn.classList.add('hidden');
                }
                setBusy(sendBtn, false);
                setBusy(verifyBtn, false);
                return;
            }

            setStatus(st.message || 'در حال پردازش...');
        }, 1000);
    }

    sendBtn.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        const identifier = identInput.value.trim();
        if (!name || !identifier) {
            setStatus('نام اکانت و شماره موبایل/ایمیل را وارد کنید.', { error: true, busy: false });
            return;
        }
        setBusy(sendBtn, true);
        setStatus('در حال اتصال به کارزار...');
        try {
            const data = await postJSON('/accounts/add/start', { name, identifier });
            sid = data.sid;
            startPolling();
        } catch (err) {
            setBusy(sendBtn, false);
            setStatus(err.message, { error: true, busy: false });
        }
    });

    verifyBtn.addEventListener('click', async () => {
        const code = otpInput.value.trim();
        if (!code) {
            setStatus('کد ورود را وارد کنید.', { error: true, busy: false });
            return;
        }
        if (!sid) return;
        setBusy(verifyBtn, true);
        setStatus('در حال تأیید کد...');
        try {
            await postJSON(`/accounts/add/otp/${sid}`, { code });
        } catch (err) {
            setBusy(verifyBtn, false);
            setStatus(err.message, { error: true, busy: false });
        }
    });

    otpInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') verifyBtn.click();
    });
    identInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') sendBtn.click();
    });

    cancelBtn.addEventListener('click', reset);
    modal.querySelectorAll('[data-modal-close]').forEach((b) => b.addEventListener('click', reset));
    modal.addEventListener('click', (e) => {
        if (e.target === modal) reset();
    });
});
