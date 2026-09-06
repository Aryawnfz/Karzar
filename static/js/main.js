// ============================================================
// مدیریت تم (دارک / لایت)
// ============================================================
(function initTheme() {
    const root = document.documentElement;
    const stored = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = stored || (prefersDark ? 'dark' : 'light');
    if (theme === 'dark') root.classList.add('dark');
})();

function toggleTheme() {
    const root = document.documentElement;
    const isDark = root.classList.toggle('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
    updateThemeToggleUI();
}

function updateThemeToggleUI() {
    const isDark = document.documentElement.classList.contains('dark');
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
        const dot = btn.querySelector('.toggle-dot');
        if (dot) {
            dot.style.transform = isDark ? 'translateX(-1.5rem)' : 'translateX(0)';
        }
        const sunIcon = btn.querySelector('[data-icon="sun"]');
        const moonIcon = btn.querySelector('[data-icon="moon"]');
        if (sunIcon && moonIcon) {
            sunIcon.classList.toggle('opacity-0', isDark);
            moonIcon.classList.toggle('opacity-0', !isDark);
        }
    });
}

// ============================================================
// اعلان شناور (Toast) سفارشی هماهنگ با تم سایت — به‌جای alert() پیش‌فرض مرورگر
// در کل سایت با window.showToast(message, type) قابل استفاده است.
// type: 'success' | 'error'
// ============================================================
const TOAST_ICONS = {
    success: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    error: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
};

function escToastText(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showToast(message, type = 'success') {
    if (typeof Swal === 'undefined') {
        window.alert(message);
        return;
    }
    Swal.mixin({
        toast: true,
        position: 'top',
        showConfirmButton: false,
        timer: 3800,
        timerProgressBar: true,
        buttonsStyling: false,
        customClass: { popup: `swal-toast swal-toast-${type}` },
        showClass: { popup: 'swal-toast-in' },
        hideClass: { popup: 'swal-toast-out' },
        didOpen: (toast) => {
            toast.onmouseenter = Swal.stopTimer;
            toast.onmouseleave = Swal.resumeTimer;
        },
    }).fire({
        html: `
            <div class="swal-toast-icon swal-toast-icon-${type}">${TOAST_ICONS[type] || TOAST_ICONS.success}</div>
            <p class="swal-toast-text">${escToastText(message)}</p>
        `,
    });
}

document.addEventListener('DOMContentLoaded', () => {
    updateThemeToggleUI();

    // ------------------------------------------------------------
    // سایدبار موبایل
    // ------------------------------------------------------------
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const openBtn = document.getElementById('open-sidebar');
    const closeBtn = document.getElementById('close-sidebar');

    function openSidebar() {
        sidebar?.classList.remove('translate-x-full');
        overlay?.classList.remove('hidden', 'opacity-0');
        requestAnimationFrame(() => overlay?.classList.remove('opacity-0'));
    }

    function closeSidebar() {
        sidebar?.classList.add('translate-x-full');
        overlay?.classList.add('opacity-0');
        setTimeout(() => overlay?.classList.add('hidden'), 300);
    }

    openBtn?.addEventListener('click', openSidebar);
    closeBtn?.addEventListener('click', closeSidebar);
    overlay?.addEventListener('click', closeSidebar);

    // ------------------------------------------------------------
    // پیام‌های فلش: بستن خودکار
    // ------------------------------------------------------------
    document.querySelectorAll('[data-flash]').forEach((el) => {
        const dismiss = () => {
            el.style.opacity = '0';
            el.style.transform = 'translateY(-8px)';
            setTimeout(() => el.remove(), 300);
        };
        setTimeout(dismiss, 4500);
        el.querySelector('[data-flash-close]')?.addEventListener('click', dismiss);
    });

    // ------------------------------------------------------------
    // مودال‌های ساده (data-modal-target / data-modal-close)
    // ------------------------------------------------------------
    document.querySelectorAll('[data-modal-open]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-modal-open');
            const modal = document.getElementById(targetId);
            if (!modal) return;
            modal.classList.remove('hidden');
            requestAnimationFrame(() => {
                modal.querySelector('[data-modal-panel]')?.classList.remove('scale-95', 'opacity-0');
            });
        });
    });

    document.querySelectorAll('[data-modal-close]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const modal = btn.closest('[data-modal]');
            closeModal(modal);
        });
    });

    document.querySelectorAll('[data-modal]').forEach((modal) => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal(modal);
        });
    });

    function closeModal(modal) {
        if (!modal) return;
        modal.querySelector('[data-modal-panel]')?.classList.add('scale-95', 'opacity-0');
        setTimeout(() => modal.classList.add('hidden'), 200);
    }

    // ------------------------------------------------------------
    // جستجو و فیلتر جدول کاربران
    // ------------------------------------------------------------
    const searchInput = document.getElementById('user-search');
    const roleFilter = document.getElementById('role-filter');
    const rows = document.querySelectorAll('[data-user-row]');

    function applyFilters() {
        const query = (searchInput?.value || '').trim().toLowerCase();
        const role = roleFilter?.value || 'all';
        let visibleCount = 0;

        rows.forEach((row) => {
            const name = row.getAttribute('data-name') || '';
            const username = row.getAttribute('data-username') || '';
            const rowRole = row.getAttribute('data-role') || '';
            const matchesQuery = name.includes(query) || username.includes(query);
            const matchesRole = role === 'all' || rowRole === role;
            const visible = matchesQuery && matchesRole;
            row.classList.toggle('hidden', !visible);
            if (visible) visibleCount++;
        });

        const emptyState = document.getElementById('users-empty-state');
        if (emptyState) emptyState.classList.toggle('hidden', visibleCount !== 0);
    }

    searchInput?.addEventListener('input', applyFilters);
    roleFilter?.addEventListener('change', applyFilters);

    // ------------------------------------------------------------
    // دراپ‌داون سفارشی (کمبوباکس)
    // ------------------------------------------------------------
    document.querySelectorAll('.custom-select').forEach((wrapper) => {
        const btn = wrapper.querySelector('.custom-select-btn');
        const panel = wrapper.querySelector('.custom-select-panel');
        const hiddenInput = wrapper.querySelector('.custom-select-value');
        const labelEl = wrapper.querySelector('.custom-select-label');
        const iconEl = wrapper.querySelector('.custom-select-icon');
        const options = wrapper.querySelectorAll('.custom-select-option');

        function closePanel() {
            panel?.classList.add('is-closed');
            btn?.setAttribute('aria-expanded', 'false');
        }

        function openPanel() {
            panel?.classList.remove('is-closed');
            btn?.setAttribute('aria-expanded', 'true');
        }

        btn?.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = btn.getAttribute('aria-expanded') === 'true';
            document.querySelectorAll('.custom-select-panel').forEach((p) => p.classList.add('is-closed'));
            isOpen ? closePanel() : openPanel();
        });

        options.forEach((opt) => {
            opt.addEventListener('click', () => {
                const value = opt.getAttribute('data-value');
                const label = opt.getAttribute('data-label');
                const iconHtml = opt.querySelector('[data-option-icon]')?.innerHTML;

                if (hiddenInput) {
                    hiddenInput.value = value;
                    hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
                }
                if (labelEl) labelEl.textContent = label;
                if (iconEl && iconHtml) iconEl.innerHTML = iconHtml;

                options.forEach((o) => o.setAttribute('data-selected', 'false'));
                opt.setAttribute('data-selected', 'true');
                closePanel();
            });
        });

        document.addEventListener('click', (e) => {
            if (!wrapper.contains(e.target)) closePanel();
        });
    });

    // ------------------------------------------------------------
    // پیش‌نمایش آواتار زنده (مودال افزودن کاربر)
    // ------------------------------------------------------------
    const newUserNameInput = document.getElementById('add-user-fullname');
    const avatarPreview = document.getElementById('add-user-avatar');
    newUserNameInput?.addEventListener('input', () => {
        const val = newUserNameInput.value.trim();
        if (avatarPreview) avatarPreview.textContent = val ? val[0] : '؟';
    });

    // ------------------------------------------------------------
    // نوار قدرت رمز عبور (مودال افزودن کاربر)
    // ------------------------------------------------------------
    const newUserPasswordInput = document.getElementById('add-user-password');
    const strengthSegments = document.querySelectorAll('[data-strength-segment]');
    const strengthLabel = document.getElementById('add-user-strength-label');

    function scorePassword(pw) {
        let score = 0;
        if (pw.length >= 4) score++;
        if (pw.length >= 8) score++;
        if (/[A-Za-z]/.test(pw) && /[0-9]/.test(pw)) score++;
        if (/[^A-Za-z0-9]/.test(pw) || pw.length >= 12) score++;
        return Math.min(score, 4);
    }

    newUserPasswordInput?.addEventListener('input', () => {
        const score = scorePassword(newUserPasswordInput.value);
        const colors = ['#ef4444', '#f2812f', '#eab308', '#14a893'];
        const labels = ['ضعیف', 'قابل قبول', 'خوب', 'عالی'];
        strengthSegments.forEach((seg, idx) => {
            const fill = seg.querySelector('span');
            if (!fill) return;
            if (newUserPasswordInput.value.length === 0) {
                fill.style.width = '0%';
                return;
            }
            fill.style.width = idx < score ? '100%' : '0%';
            fill.style.backgroundColor = colors[Math.max(score - 1, 0)];
        });
        if (strengthLabel) {
            strengthLabel.textContent = newUserPasswordInput.value.length === 0 ? '' : labels[Math.max(score - 1, 0)];
            strengthLabel.style.color = newUserPasswordInput.value.length === 0 ? '' : colors[Math.max(score - 1, 0)];
        }
    });

    // ------------------------------------------------------------
    // انتخاب کارت اکانت در صفحه «ثبت امضا»
    // ------------------------------------------------------------
    document.querySelectorAll('.account-checkbox').forEach((cb) => {
        cb.addEventListener('change', updateSelectedCount);
    });

    function updateSelectedCount() {
        const checked = document.querySelectorAll('.account-checkbox:checked').length;
        const counter = document.getElementById('selected-count');
        const submitBtn = document.getElementById('submit-signature-btn');
        if (counter) counter.textContent = checked;
        if (submitBtn) submitBtn.disabled = checked === 0;
        if (submitBtn) submitBtn.classList.toggle('opacity-50', checked === 0);
        if (submitBtn) submitBtn.classList.toggle('cursor-not-allowed', checked === 0);
    }
    updateSelectedCount();

    const selectAllBtn = document.getElementById('select-all-accounts');
    selectAllBtn?.addEventListener('click', () => {
        const boxes = document.querySelectorAll('.account-checkbox');
        const allChecked = [...boxes].every((b) => b.checked);
        boxes.forEach((b) => (b.checked = !allChecked));
        selectAllBtn.textContent = allChecked ? 'انتخاب همه' : 'لغو انتخاب همه';
        updateSelectedCount();
    });
});
