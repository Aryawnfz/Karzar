// ============================================================
// ثبت امضا: شروع کار در پس‌زمینه → پایش وضعیت → نمایش نتیجه روی کارت هر اکانت
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('signature-form');
    if (!form) return;

    const linkInput = document.getElementById('link_or_code');
    const formError = document.getElementById('signature-form-error');
    const submitBtn = document.getElementById('submit-signature-btn');
    const submitText = submitBtn.querySelector('[data-submit-text]');

    const progress = document.getElementById('sign-progress');
    const progressSpinner = document.getElementById('sign-progress-spinner');
    const progressTitle = document.getElementById('sign-progress-title');
    const progressLink = document.getElementById('sign-progress-link');
    const progressDone = document.getElementById('sign-progress-done');
    const progressTotal = document.getElementById('sign-progress-total');
    const progressFailedWrap = document.getElementById('sign-progress-failed-wrap');
    const progressFailed = document.getElementById('sign-progress-failed');
    const progressBar = document.getElementById('sign-progress-bar');

    let jid = null;
    let pollTimer = null;
    let running = false;

    const BADGE = {
        pending: { text: 'در صف', cls: 'bg-slate-100 dark:bg-white/5 text-slate-500 dark:text-slate-400', dot: 'bg-slate-400' },
        running: { text: 'در حال انجام', cls: 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400', dot: 'bg-amber-500 animate-pulse' },
        done:    { text: 'تکمیل شد', cls: 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400', dot: 'bg-emerald-500' },
        failed:  { text: 'ناموفق', cls: 'bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400', dot: 'bg-red-500' },
    };
    const BASE_BADGE = 'mt-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold';
    const BASE_DOT = 'h-1.5 w-1.5 rounded-full';

    function showError(msg) {
        formError.textContent = msg;
        formError.classList.remove('hidden');
    }
    function hideError() {
        formError.classList.add('hidden');
    }

    function setRunning(on) {
        running = on;
        submitBtn.disabled = on || document.querySelectorAll('.account-checkbox:checked').length === 0;
        submitBtn.classList.toggle('opacity-50', submitBtn.disabled);
        submitBtn.classList.toggle('cursor-not-allowed', submitBtn.disabled);
        submitText.textContent = on ? 'در حال ثبت امضا...' : 'ثبت امضا';
        linkInput.disabled = on;
        document.querySelectorAll('.account-checkbox').forEach((cb) => (cb.disabled = on));
        document.querySelectorAll('.account-card').forEach((c) => c.classList.toggle('is-locked', on));
        const selectAll = document.getElementById('select-all-accounts');
        if (selectAll) selectAll.disabled = on;
    }

    function cardFor(id) {
        return document.querySelector(`[data-account-card="${id}"]`);
    }

    function renderAccount(acc) {
        const card = cardFor(acc.id);
        if (!card) return;
        const b = BADGE[acc.status] || BADGE.pending;
        const badge = card.querySelector('[data-sign-badge]');
        const dot = card.querySelector('[data-sign-dot]');
        const badgeText = card.querySelector('[data-sign-badge-text]');
        badge.className = `${BASE_BADGE} ${b.cls}`;
        dot.className = `${BASE_DOT} ${b.dot}`;
        badgeText.textContent = b.text;

        const result = card.querySelector('[data-sign-result]');
        const msg = card.querySelector('[data-sign-message]');
        const shotLink = card.querySelector('[data-sign-shot-link]');
        const shot = card.querySelector('[data-sign-shot]');

        result.classList.remove('hidden');
        msg.textContent = acc.message || '';
        msg.classList.toggle('text-red-500', acc.status === 'failed');
        msg.classList.toggle('text-emerald-600', acc.status === 'done');
        msg.classList.toggle('dark:text-emerald-400', acc.status === 'done');

        if (acc.screenshot && jid) {
            const url = `/signatures/screenshot/${jid}/${acc.id}`;
            if (shot.getAttribute('src') !== url) shot.src = url;
            shotLink.href = url;
            shotLink.classList.remove('hidden');
        } else {
            shotLink.classList.add('hidden');
        }
    }

    function renderJob(job) {
        const total = job.accounts.length;
        const done = job.accounts.filter((a) => a.status === 'done').length;
        const failed = job.accounts.filter((a) => a.status === 'failed').length;

        progress.classList.remove('hidden');
        progressLink.textContent = job.campaign_url;
        progressLink.href = job.campaign_url;
        progressTotal.textContent = total;
        progressDone.textContent = done;
        progressFailed.textContent = failed;
        progressFailedWrap.classList.toggle('hidden', failed === 0);
        progressBar.style.width = `${Math.round(((done + failed) / Math.max(total, 1)) * 100)}%`;

        job.accounts.forEach(renderAccount);

        if (job.status === 'finished') {
            progressSpinner.classList.add('hidden');
            progressTitle.textContent = failed === 0
                ? 'ثبت امضا برای همهٔ اکانت‌ها تکمیل شد.'
                : `ثبت امضا پایان یافت (${done} موفق، ${failed} ناموفق).`;
        } else {
            progressSpinner.classList.remove('hidden');
            const cur = job.accounts.find((a) => a.status === 'running');
            progressTitle.textContent = cur ? `در حال ثبت امضا با «${cur.name}»...` : 'در حال ثبت امضا...';
        }
    }

    function resetCards() {
        document.querySelectorAll('[data-account-card]').forEach((card) => {
            card.querySelector('[data-sign-result]').classList.add('hidden');
            card.querySelector('[data-sign-shot-link]').classList.add('hidden');
            card.querySelector('[data-sign-shot]').removeAttribute('src');
        });
    }

    function stopPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
    }

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(async () => {
            if (!jid) return stopPolling();
            try {
                const res = await fetch(`/signatures/status/${jid}`, { cache: 'no-store' });
                if (res.status === 404) {
                    stopPolling();
                    setRunning(false);
                    showError('کار ثبت امضا پیدا نشد.');
                    return;
                }
                const data = await res.json();
                if (!data.ok) return;
                renderJob(data.job);
                if (data.job.status === 'finished') {
                    stopPolling();
                    setRunning(false);
                }
            } catch (e) {
                /* خطای شبکهٔ موقتی؛ در نوبت بعد دوباره تلاش می‌شود */
            }
        }, 1500);
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (running) return;
        hideError();

        const link_or_code = linkInput.value.trim();
        const account_ids = [...document.querySelectorAll('.account-checkbox:checked')].map((cb) => cb.value);

        if (!link_or_code) return showError('لینک یا کد کارزار را وارد کنید.');
        if (account_ids.length === 0) return showError('حداقل یک اکانت را انتخاب کنید.');

        setRunning(true);
        resetCards();
        try {
            const res = await fetch(form.action, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ link_or_code, account_ids }),
            });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                setRunning(false);
                return showError(data.error || 'خطا در شروع ثبت امضا.');
            }
            jid = data.jid;
            startPolling();
        } catch (err) {
            setRunning(false);
            showError('ارتباط با سرور برقرار نشد.');
        }
    });
});
