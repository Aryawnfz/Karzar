// ============================================================
// گزارش فعالیت‌ها: فیلتر (چندکاربره)، جستجو، نمودار، جدول، خروجی، به‌روزرسانی زنده
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    const page = document.getElementById('activity-page');
    if (!page) return;

    const isAdmin = page.dataset.isAdmin === 'true';
    const USERS = JSON.parse(page.dataset.users || '[]');
    const LEVELS = JSON.parse(page.dataset.levels || '[]');
    const CATEGORIES = JSON.parse(page.dataset.categories || '[]');

    const $ = (id) => document.getElementById(id);
    const el = {
        users: $('act-users'), userSearch: $('act-user-search'),
        levels: $('act-levels'), categories: $('act-categories'),
        q: $('act-q'), from: $('act-from'), to: $('act-to'), debug: $('act-debug'),
        perPage: $('act-per-page'), rows: $('act-rows'), empty: $('act-empty'),
        pageInfo: $('act-page-info'), pageNum: $('act-page-num'),
        first: $('act-first'), prev: $('act-prev'), next: $('act-next'), last: $('act-last'),
        total: $('act-total'), range: $('act-range'), loading: $('act-loading'), liveDot: $('act-live-dot'),
        live: $('act-live'), refresh: $('act-refresh'), reset: $('act-reset'), clear: $('act-clear'),
        exportCsv: $('act-export-csv'), exportXlsx: $('act-export-xlsx'), exportJson: $('act-export-json'), granularity: $('act-granularity'),
    };

    // ---------------- انتخابگر تاریخ/ساعت شمسی سفارشی ----------------
    const jdpFrom = window.JalaliDateTimePicker
        ? new JalaliDateTimePicker(el.from, $('act-from-display'), { enableTime: true })
        : null;
    const jdpTo = window.JalaliDateTimePicker
        ? new JalaliDateTimePicker(el.to, $('act-to-display'), { enableTime: true })
        : null;

    const LEVEL_STYLE = {
        debug:    { badge: 'bg-slate-100 dark:bg-white/5 text-slate-500 dark:text-slate-400', color: '#94a3b8' },
        info:     { badge: 'bg-sky-50 dark:bg-sky-500/10 text-sky-600 dark:text-sky-400', color: '#0ea5e9' },
        warning:  { badge: 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400', color: '#f59e0b' },
        error:    { badge: 'bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400', color: '#ef4444' },
        security: { badge: 'bg-fuchsia-50 dark:bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400', color: '#d946ef' },
    };
    const CAT_COLORS = ['#14a893', '#f2812f', '#0ea5e9', '#8b5cf6', '#ec4899', '#64748b', '#22c55e'];
    const LEVEL_LABEL = Object.fromEntries(LEVELS.map((l) => [l.key, l.label]));
    const CAT_LABEL = Object.fromEntries(CATEGORIES.map((c) => [c.key, c.label]));

    // ---------------- وضعیت فیلتر (همگام با URL) ----------------
    const state = {
        users: new Set(), levels: new Set(), categories: new Set(),
        q: '', from: '', to: '', debug: false, page: 1, perPage: 50,
    };
    let pollTimer = null;
    let inflight = null;
    let expandedId = null;
    let lastItems = [];

    function readUrl() {
        const p = new URLSearchParams(location.search);
        p.getAll('user').forEach((u) => state.users.add(u));
        p.getAll('level').forEach((l) => state.levels.add(l));
        p.getAll('category').forEach((c) => state.categories.add(c));
        state.q = p.get('q') || '';
        state.from = p.get('from') || '';
        state.to = p.get('to') || '';
        state.debug = p.get('debug') === '1';
        state.page = parseInt(p.get('page') || '1', 10) || 1;
        state.perPage = parseInt(p.get('per_page') || '50', 10) || 50;
    }

    function buildParams(withPaging) {
        const p = new URLSearchParams();
        state.users.forEach((u) => p.append('user', u));
        state.levels.forEach((l) => p.append('level', l));
        state.categories.forEach((c) => p.append('category', c));
        if (state.q) p.set('q', state.q);
        if (state.from) p.set('from', state.from);
        if (state.to) p.set('to', state.to);
        if (!state.debug && state.levels.size === 0) p.set('min_level', 'info');
        if (withPaging) {
            p.set('page', state.page);
            p.set('per_page', state.perPage);
        }
        return p;
    }

    function syncUrl() {
        const p = buildParams(true);
        p.delete('min_level');
        if (state.debug) p.set('debug', '1');
        history.replaceState(null, '', `${location.pathname}?${p.toString()}`);
        const exp = buildParams(false).toString();
        el.exportCsv.href = `/activity/export.csv?${exp}`;
        el.exportXlsx.href = `/activity/export.xlsx?${exp}`;
        el.exportJson.href = `/activity/export.json?${exp}`;
    }

    // ---------------- رندر کنترل‌های فیلتر ----------------
    function chip(key, label, active, extraCls) {
        const b = document.createElement('button');
        b.type = 'button';
        b.dataset.key = key;
        b.className = `rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition-colors ${
            active ? 'border-primary-400 bg-primary-50 dark:bg-primary-500/10 text-primary-700 dark:text-primary-300'
                   : 'border-slate-200 dark:border-white/10 text-slate-500 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/5'} ${extraCls || ''}`;
        b.textContent = label;
        return b;
    }

    function renderUsers() {
        const term = (el.userSearch.value || '').trim().toLowerCase();
        el.users.innerHTML = '';
        const list = [...USERS];
        if (isAdmin) list.push({ id: 'anonymous', username: '', full_name: 'ناشناس / بدون ورود', role: '' });
        list.filter((u) => !term || `${u.full_name} ${u.username}`.toLowerCase().includes(term)).forEach((u) => {
            const id = String(u.id);
            const row = document.createElement('label');
            row.className = 'fancy-checkbox flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-slate-50 dark:hover:bg-white/5';
            row.innerHTML = `
                <input type="checkbox" value="${id}" class="fc-input peer sr-only" ${state.users.has(id) ? 'checked' : ''}>
                <span class="fc-box" style="width:1.15rem;height:1.15rem;">
                    <svg class="fc-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" style="width:0.7rem;height:0.7rem;"><path d="M4 12l5 5L20 6"/></svg>
                </span>
                <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-secondary-400 to-secondary-600 text-[10px] font-bold text-white">${(u.full_name || '؟')[0]}</span>
                <span class="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-200">${esc(u.full_name)}</span>
                ${u.username ? `<span dir="ltr" class="font-mono text-[10px] text-slate-400">${esc(u.username)}</span>` : ''}
                ${u.role === 'admin' ? '<span class="rounded-md bg-primary-50 dark:bg-primary-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary-600 dark:text-primary-300">مدیر</span>' : ''}`;
            row.querySelector('input').addEventListener('change', (e) => {
                e.target.checked ? state.users.add(id) : state.users.delete(id);
                changed();
            });
            el.users.appendChild(row);
        });
    }

    function renderChips() {
        el.levels.innerHTML = '';
        LEVELS.forEach((l) => {
            const c = chip(l.key, l.label, state.levels.has(l.key));
            c.addEventListener('click', () => { toggle(state.levels, l.key); changed(); });
            el.levels.appendChild(c);
        });
        el.categories.innerHTML = '';
        CATEGORIES.forEach((cat) => {
            const c = chip(cat.key, cat.label, state.categories.has(cat.key));
            c.addEventListener('click', () => { toggle(state.categories, cat.key); changed(); });
            el.categories.appendChild(c);
        });
    }

    function toggle(set, key) { set.has(key) ? set.delete(key) : set.add(key); }

    function syncInputs() {
        el.q.value = state.q;
        if (jdpFrom) jdpFrom.setValue(state.from); else el.from.value = state.from;
        if (jdpTo) jdpTo.setValue(state.to); else el.to.value = state.to;
        el.debug.checked = state.debug;
        el.perPage.value = String(state.perPage);
    }

    // ---------------- نمودارها ----------------
    const charts = {};
    const isDark = () => document.documentElement.classList.contains('dark');
    const gridColor = () => (isDark() ? 'rgba(255,255,255,0.06)' : 'rgba(15,30,45,0.06)');
    const tickColor = () => (isDark() ? '#94a3b8' : '#64748b');

    function baseOpts(extra) {
        return Object.assign({
            responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
            plugins: { legend: { labels: { color: tickColor(), font: { family: 'Cairo' }, boxWidth: 10 } }, tooltip: { rtl: true, bodyFont: { family: 'Cairo' }, titleFont: { family: 'Cairo' } } },
        }, extra || {});
    }
    function axes(stacked) {
        return {
            x: { stacked, grid: { color: gridColor() }, ticks: { color: tickColor(), font: { family: 'Cairo', size: 10 }, maxRotation: 0, autoSkip: true } },
            y: { stacked, beginAtZero: true, grid: { color: gridColor() }, ticks: { color: tickColor(), font: { family: 'Cairo', size: 10 }, precision: 0 } },
        };
    }

    function upsert(name, canvasId, config) {
        if (charts[name]) {
            charts[name].data = config.data;
            charts[name].options = config.options;
            charts[name].update();
        } else {
            charts[name] = new Chart(document.getElementById(canvasId), config);
        }
    }

    function fmtBucket(b, gran) {
        if (gran === 'day') return b.slice(5);
        if (gran === 'hour') return `${b.slice(5, 10)} ${b.slice(11)}`;
        return b.slice(11);
    }

    function renderCharts(stats) {
        const tl = stats.timeline || [];
        const gran = stats.granularity;
        el.granularity.textContent = { minute: 'تفکیک: دقیقه', hour: 'تفکیک: ساعت', day: 'تفکیک: روز' }[gran] || '';
        upsert('timeline', 'chart-timeline', {
            type: 'bar',
            data: {
                labels: tl.map((t) => fmtBucket(t.bucket, gran)),
                datasets: [
                    { label: 'عادی', data: tl.map((t) => t.total - t.errors - t.warnings - t.security), backgroundColor: '#14a893', borderRadius: 4 },
                    { label: 'هشدار', data: tl.map((t) => t.warnings), backgroundColor: LEVEL_STYLE.warning.color, borderRadius: 4 },
                    { label: 'خطا', data: tl.map((t) => t.errors), backgroundColor: LEVEL_STYLE.error.color, borderRadius: 4 },
                    { label: 'امنیتی', data: tl.map((t) => t.security), backgroundColor: LEVEL_STYLE.security.color, borderRadius: 4 },
                ],
            },
            options: baseOpts({ scales: axes(true), interaction: { mode: 'index', intersect: false } }),
        });

        const lv = LEVELS.filter((l) => (stats.by_level[l.key] || 0) > 0);
        upsert('levels', 'chart-levels', {
            type: 'doughnut',
            data: { labels: lv.map((l) => l.label), datasets: [{ data: lv.map((l) => stats.by_level[l.key]), backgroundColor: lv.map((l) => LEVEL_STYLE[l.key].color), borderWidth: 0 }] },
            options: baseOpts({ cutout: '65%', plugins: { legend: { position: 'bottom', labels: { color: tickColor(), font: { family: 'Cairo' }, boxWidth: 10 } } } }),
        });

        const users = (stats.by_user || []).slice(0, 8);
        upsert('users', 'chart-users', {
            type: 'bar',
            data: {
                labels: users.map((u) => u.full_name || u.username || 'ناشناس'),
                datasets: [
                    { label: 'رویداد', data: users.map((u) => u.count - u.errors), backgroundColor: '#f2812f', borderRadius: 4 },
                    { label: 'خطا/امنیتی', data: users.map((u) => u.errors), backgroundColor: LEVEL_STYLE.error.color, borderRadius: 4 },
                ],
            },
            options: baseOpts({ indexAxis: 'y', scales: axes(true) }),
        });

        const cats = CATEGORIES.filter((c) => (stats.by_category[c.key] || 0) > 0);
        upsert('categories', 'chart-categories', {
            type: 'polarArea',
            data: { labels: cats.map((c) => c.label), datasets: [{ data: cats.map((c) => stats.by_category[c.key]), backgroundColor: cats.map((_, i) => CAT_COLORS[i % CAT_COLORS.length] + 'cc'), borderWidth: 0 }] },
            options: baseOpts({ scales: { r: { grid: { color: gridColor() }, ticks: { display: false } } }, plugins: { legend: { position: 'bottom', labels: { color: tickColor(), font: { family: 'Cairo' }, boxWidth: 10 } } } }),
        });

        const acts = (stats.top_actions || []).slice(0, 10);
        upsert('actions', 'chart-actions', {
            type: 'bar',
            data: { labels: acts.map((a) => a.action), datasets: [{ label: 'تعداد', data: acts.map((a) => a.count), backgroundColor: '#8b5cf6', borderRadius: 4 }] },
            options: baseOpts({ indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: axes(false).x, y: { grid: { display: false }, ticks: { color: tickColor(), font: { family: 'monospace', size: 10 } } } } }),
        });
    }

    // ---------------- KPI ----------------
    function fmtMs(v) {
        if (v == null) return '—';
        return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
    }
    // تبدیل هر برچسب زمانی میلادی (خروجی سرور) به رشتهٔ شمسی برای نمایش کنار تاریخ میلادی
    function jalaliOf(isoLike, withSeconds) {
        if (!isoLike || !window.jalaliDateUtil) return '';
        const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(isoLike);
        if (!m) return '';
        const j = window.jalaliDateUtil.toJalali(+m[1], +m[2], +m[3]);
        const pad2 = (n) => (n < 10 ? '0' : '') + n;
        let str = `${j.jy}/${pad2(j.jm)}/${pad2(j.jd)} ${m[4]}:${m[5]}`;
        if (withSeconds && m[6] !== undefined) str += ':' + m[6];
        return window.jalaliDateUtil.toFaDigits(str);
    }
    function renderKpis(stats) {
        $('kpi-total').textContent = stats.total.toLocaleString('fa-IR');
        ['info', 'warning', 'error', 'security'].forEach((k) => ($(`kpi-${k}`).textContent = (stats.by_level[k] || 0).toLocaleString('fa-IR')));
        $('kpi-users').textContent = (stats.by_user || []).filter((u) => u.user_id != null).length.toLocaleString('fa-IR');
        $('kpi-avg').textContent = fmtMs(stats.avg_duration_ms);
        $('kpi-p95').textContent = fmtMs(stats.p95_duration_ms);
        el.total.textContent = stats.total.toLocaleString('fa-IR');
        if (stats.first_ts) {
            const gFrom = stats.first_ts.replace('T', ' ').slice(0, 16);
            const gTo = stats.last_ts.replace('T', ' ').slice(0, 16);
            const jFrom = jalaliOf(stats.first_ts, false);
            const jTo = jalaliOf(stats.last_ts, false);
            el.range.textContent = `(${jFrom || gFrom} تا ${jTo || gTo}${jFrom ? ` / ${gFrom} تا ${gTo}` : ''})`;
        } else {
            el.range.textContent = '';
        }
    }

    // ---------------- جدول ----------------
    function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // تاییدیهٔ حذف با ظاهر سفارشی (SweetAlert2) به‌جای confirm() پیش‌فرض مرورگر
    function confirmDelete(title, text) {
        if (typeof Swal === 'undefined') return Promise.resolve(window.confirm(text || title));
        return Swal.fire({
            html: `
                <div class="swal-icon-warn">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </div>
                <h2 class="swal-glass-title">${esc(title)}</h2>
                <p class="swal-glass-text">${esc(text)}</p>
            `,
            showCancelButton: true,
            confirmButtonText: 'بله، حذف شود',
            cancelButtonText: 'انصراف',
            reverseButtons: true,
            focusCancel: true,
            buttonsStyling: false,
            customClass: {
                container: 'swal-backdrop',
                popup: 'swal-glass',
                confirmButton: 'swal-btn-danger',
                cancelButton: 'swal-btn-cancel',
            },
            showClass: { popup: 'swal-anim-in' },
            hideClass: { popup: 'swal-anim-out' },
        }).then((r) => r.isConfirmed);
    }
    function statusBadge(code) {
        if (!code) return '<span class="text-slate-300 dark:text-slate-600">—</span>';
        const cls = code >= 500 ? 'text-red-500' : code >= 400 ? 'text-amber-500' : code >= 300 ? 'text-sky-500' : 'text-emerald-500';
        return `<span dir="ltr" class="font-mono font-bold ${cls}">${code}</span>`;
    }

    function renderRows(items) {
        lastItems = items;
        el.rows.innerHTML = '';
        el.empty.classList.toggle('hidden', items.length > 0);
        items.forEach((ev) => {
            const tr = document.createElement('tr');
            tr.dataset.id = ev.id;
            tr.className = 'cursor-pointer align-top hover:bg-slate-50/70 dark:hover:bg-white/[0.03] transition-colors';
            const ls = LEVEL_STYLE[ev.level] || LEVEL_STYLE.info;
            const t = (ev.ts || '').replace('T', ' ');
            const tj = jalaliOf(ev.ts, true);
            tr.innerHTML = `
                <td class="whitespace-nowrap px-4 py-2.5 text-right text-[11px] text-slate-700 dark:text-slate-200">
                    ${tj ? `<div dir="ltr" class="fa-nums font-semibold">${esc(tj)}</div>` : ''}
                    <div dir="ltr" class="mt-0.5 font-mono text-[10px] text-slate-400 dark:text-slate-500">${esc(t.slice(0, 19))}<span class="text-slate-300 dark:text-slate-600">${esc(t.slice(19))}</span></div>
                </td>
                <td class="px-3 py-2.5"><span class="inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold ${ls.badge}">${esc(LEVEL_LABEL[ev.level] || ev.level)}</span></td>
                <td class="whitespace-nowrap px-3 py-2.5 text-slate-500 dark:text-slate-400">${esc(CAT_LABEL[ev.category] || ev.category)}</td>
                <td class="whitespace-nowrap px-3 py-2.5">
                    ${ev.user_id != null
                        ? `<span class="font-semibold text-slate-700 dark:text-slate-200">${esc(ev.full_name || ev.username)}</span> <span dir="ltr" class="font-mono text-[10px] text-slate-400">${esc(ev.username || '')}</span>`
                        : '<span class="text-slate-400">ناشناس</span>'}
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 text-right font-mono text-[11px] text-primary-600 dark:text-primary-400" dir="ltr">${esc(ev.action)}</td>
                <td class="max-w-md px-3 py-2.5 text-slate-700 dark:text-slate-200"><div class="truncate" title="${esc(ev.message)}">${esc(ev.message)}</div>${ev.path ? `<div dir="ltr" class="truncate text-right font-mono text-[10px] text-slate-400">${esc(ev.method || '')} ${esc(ev.path)}</div>` : ''}</td>
                <td class="whitespace-nowrap px-3 py-2.5 text-right font-mono text-[11px] text-slate-500 dark:text-slate-400" dir="ltr">${esc(ev.ip || '—')}</td>
                <td class="whitespace-nowrap px-3 py-2.5">${statusBadge(ev.status_code)}</td>
                <td class="whitespace-nowrap px-3 py-2.5 text-right font-mono text-[11px] text-slate-500 dark:text-slate-400" dir="ltr">${ev.duration_ms != null ? fmtMs(ev.duration_ms) : '—'}</td>
                <td class="px-3 py-2.5 text-slate-300 dark:text-slate-600"><svg class="h-4 w-4 transition-transform ${expandedId === ev.id ? 'rotate-180' : ''}" data-chev viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg></td>`;
            tr.addEventListener('click', () => toggleDetails(ev, tr));
            el.rows.appendChild(tr);
            if (expandedId === ev.id) el.rows.appendChild(detailRow(ev));
        });
    }

    function detailRow(ev) {
        const tr = document.createElement('tr');
        tr.dataset.detailFor = ev.id;
        tr.className = 'bg-slate-50/60 dark:bg-white/[0.02]';
        const meta = [
            ['شناسهٔ رویداد', ev.id], ['شناسهٔ درخواست', ev.request_id], ['نقش', ev.role], ['Endpoint', ev.endpoint],
            ['PID', ev.pid], ['User-Agent', ev.user_agent],
        ].filter(([, v]) => v != null && v !== '');
        tr.innerHTML = `
            <td colspan="10" class="px-5 py-4">
                <div class="grid gap-4 lg:grid-cols-2">
                    <div class="space-y-1.5 text-[11px]">
                        ${meta.map(([k, v]) => `<div class="flex gap-2"><span class="w-28 shrink-0 text-slate-400">${esc(k)}</span><span dir="ltr" class="break-all font-mono text-slate-600 dark:text-slate-300">${esc(v)}</span></div>`).join('')}
                    </div>
                    <div>
                        <p class="mb-1 text-[11px] font-bold text-slate-500 dark:text-slate-400">جزئیات (details)</p>
                        <pre dir="ltr" class="max-h-64 overflow-auto rounded-xl bg-slate-900 dark:bg-black/40 p-3 text-[11px] leading-relaxed text-emerald-200">${esc(JSON.stringify(ev.details || {}, null, 2))}</pre>
                    </div>
                </div>
            </td>`;
        return tr;
    }

    function toggleDetails(ev, tr) {
        const existing = el.rows.querySelector(`[data-detail-for="${ev.id}"]`);
        el.rows.querySelectorAll('[data-detail-for]').forEach((r) => r.remove());
        el.rows.querySelectorAll('[data-chev]').forEach((c) => c.classList.remove('rotate-180'));
        if (existing) { expandedId = null; return; }
        expandedId = ev.id;
        tr.querySelector('[data-chev]').classList.add('rotate-180');
        tr.after(detailRow(ev));
    }

    function renderPaging(d) {
        el.pageNum.textContent = `${d.page.toLocaleString('fa-IR')} / ${d.pages.toLocaleString('fa-IR')}`;
        const start = d.total ? (d.page - 1) * d.per_page + 1 : 0;
        const end = Math.min(d.total, d.page * d.per_page);
        el.pageInfo.textContent = d.total ? `نمایش ${start.toLocaleString('fa-IR')} تا ${end.toLocaleString('fa-IR')} از ${d.total.toLocaleString('fa-IR')} رویداد` : '';
        el.first.disabled = el.prev.disabled = d.page <= 1;
        el.next.disabled = el.last.disabled = d.page >= d.pages;
        state.page = d.page;
        el.last.dataset.pages = d.pages;
    }

    // ---------------- بارگذاری ----------------
    async function load(silent) {
        if (inflight) inflight.abort();
        inflight = new AbortController();
        if (!silent) el.loading.classList.remove('hidden');
        syncUrl();
        try {
            const res = await fetch(`/activity/events?${buildParams(true).toString()}`, { cache: 'no-store', signal: inflight.signal });
            const d = await res.json();
            if (!d.ok) throw new Error('bad response');
            renderKpis(d.stats);
            renderCharts(d.stats);
            renderRows(d.items);
            renderPaging(d);
            el.liveDot.className = 'h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse';
        } catch (e) {
            if (e.name !== 'AbortError') el.liveDot.className = 'h-2.5 w-2.5 rounded-full bg-red-500';
        } finally {
            el.loading.classList.add('hidden');
        }
    }

    let debounce = null;
    function changed(resetPage = true) {
        if (resetPage) state.page = 1;
        renderChips();
        clearTimeout(debounce);
        debounce = setTimeout(() => load(false), 150);
    }

    function setLive(on) {
        clearInterval(pollTimer);
        pollTimer = null;
        if (on) pollTimer = setInterval(() => { if (document.visibilityState === 'visible') load(true); }, 10000);
    }

    // ---------------- رویدادها ----------------
    el.userSearch.addEventListener('input', renderUsers);
    el.q.addEventListener('input', () => { state.q = el.q.value.trim(); changed(); });
    el.from.addEventListener('change', () => { state.from = el.from.value; changed(); });
    el.to.addEventListener('change', () => { state.to = el.to.value; changed(); });
    el.debug.addEventListener('change', () => { state.debug = el.debug.checked; changed(); });
    el.perPage.addEventListener('change', () => { state.perPage = parseInt(el.perPage.value, 10); changed(); });
    el.first.addEventListener('click', () => { state.page = 1; load(false); });
    el.prev.addEventListener('click', () => { state.page = Math.max(1, state.page - 1); load(false); });
    el.next.addEventListener('click', () => { state.page += 1; load(false); });
    el.last.addEventListener('click', () => { state.page = parseInt(el.last.dataset.pages || '1', 10); load(false); });
    el.refresh.addEventListener('click', () => load(false));
    el.live.addEventListener('change', () => setLive(el.live.checked));

    const usersAll = $('act-users-all');
    const usersNone = $('act-users-none');
    if (usersAll) usersAll.addEventListener('click', () => { USERS.forEach((u) => state.users.add(String(u.id))); state.users.add('anonymous'); renderUsers(); changed(); });
    if (usersNone) usersNone.addEventListener('click', () => { state.users.clear(); renderUsers(); changed(); });

    const pad = (n) => String(n).padStart(2, '0');
    const toLocal = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    document.querySelectorAll('[data-quick-range]').forEach((b) => {
        b.addEventListener('click', () => {
            const k = b.dataset.quickRange;
            const now = new Date();
            const ms = { '1h': 3600e3, '24h': 86400e3, '7d': 7 * 86400e3, '30d': 30 * 86400e3 }[k];
            state.from = ms ? toLocal(new Date(now - ms)) : '';
            state.to = '';
            syncInputs();
            changed();
        });
    });

    el.reset.addEventListener('click', () => {
        state.users.clear(); state.levels.clear(); state.categories.clear();
        state.q = ''; state.from = ''; state.to = ''; state.debug = false; state.page = 1;
        syncInputs(); renderUsers(); changed();
    });

    if (el.clear) {
        el.clear.addEventListener('click', async () => {
            const ok = await confirmDelete('پاک‌سازی کل لاگ‌ها؟', 'همهٔ لاگ‌های فعالیت به‌طور دائمی حذف می‌شوند. این عمل بازگشت‌ناپذیر است.');
            if (!ok) return;
            el.clear.disabled = true;
            try {
                const res = await fetch('/activity/clear', { method: 'POST' });
                const d = await res.json();
                if (!d.ok) showToast(d.error || 'پاک‌سازی انجام نشد.', 'error');
                else showToast('همهٔ لاگ‌ها با موفقیت پاک‌سازی شد.', 'success');
            } catch (e) {
                showToast('ارتباط با سرور برقرار نشد.', 'error');
            } finally {
                el.clear.disabled = false;
                load(false);
            }
        });
    }

    // بازترسیم نمودارها با تغییر تم
    new MutationObserver(() => Object.values(charts).forEach((c) => {
        c.options.plugins.legend.labels.color = tickColor();
        if (c.options.scales) Object.values(c.options.scales).forEach((s) => { if (s.grid) s.grid.color = gridColor(); if (s.ticks) s.ticks.color = tickColor(); });
        c.update();
    })).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    // ---------------- شروع ----------------
    readUrl();
    if (!isAdmin && USERS.length === 1) state.users.clear();
    syncInputs();
    renderUsers();
    renderChips();
    load(false);
    setLive(el.live.checked);
});
