// ============================================================
// انتخابگر تاریخ/ساعت شمسی (جلالی) — بدون هیچ وابستگی بیرونی
// شامل: تبدیل تقویم جلالی <-> میلادی، و ویجت پاپ‌آپ کاملاً سفارشی
// (هیچ عنصر بومی مرورگر مثل select یا input[type=date] استفاده نمی‌شود
//  که خودمان بتوانیم ظاهرش را صددرصد کنترل کنیم)
// ============================================================
(function (global) {
    'use strict';

    // ---------------- تبدیل تقویم (الگوریتم نجومی استاندارد ۳۳ ساله) ----------------
    function intDiv(a, b) { return Math.trunc(a / b); }
    function mod(a, b) { return a - intDiv(a, b) * b; }

    var JALALI_BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];

    function jalaliCalInfo(jy) {
        var breaksLen = JALALI_BREAKS.length;
        var gy = jy + 621;
        var leapJ = -14;
        var prevBreak = JALALI_BREAKS[0];
        var jump = 0;
        var i, curBreak;

        if (jy < prevBreak || jy >= JALALI_BREAKS[breaksLen - 1]) {
            throw new RangeError('سال جلالی خارج از محدوده پشتیبانی‌شده است: ' + jy);
        }
        for (i = 1; i < breaksLen; i += 1) {
            curBreak = JALALI_BREAKS[i];
            jump = curBreak - prevBreak;
            if (jy < curBreak) break;
            leapJ += intDiv(jump, 33) * 8 + intDiv(mod(jump, 33), 4);
            prevBreak = curBreak;
        }
        var n = jy - prevBreak;
        leapJ += intDiv(n, 33) * 8 + intDiv(mod(n, 33) + 3, 4);
        if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;

        var leapG = intDiv(gy, 4) - intDiv((intDiv(gy, 100) + 1) * 3, 4) - 150;
        var march = 20 + leapJ - leapG;

        if (jump - n < 6) n = n - jump + intDiv(jump + 4, 33) * 33;
        var leap = mod(mod(n + 1, 33) - 1, 4);
        if (leap === -1) leap = 4;

        return { leap: leap, gy: gy, march: march };
    }

    function gregorianToJDN(gy, gm, gd) {
        var d = intDiv((gy + intDiv(gm - 8, 6) + 100100) * 1461, 4)
            + intDiv(153 * mod(gm + 9, 12) + 2, 5)
            + gd - 34840408;
        d = d - intDiv(intDiv(gy + 100100 + intDiv(gm - 8, 6), 100) * 3, 4) + 752;
        return d;
    }

    function jdnToGregorian(jdn) {
        var j = 4 * jdn + 139361631;
        j += intDiv(intDiv(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
        var i = intDiv(mod(j, 1461), 4) * 5 + 308;
        var gd = intDiv(mod(i, 153), 5) + 1;
        var gm = mod(intDiv(i, 153), 12) + 1;
        var gy = intDiv(j, 1461) - 100100 + intDiv(8 - gm, 6);
        return { gy: gy, gm: gm, gd: gd };
    }

    function jalaliToJDN(jy, jm, jd) {
        var info = jalaliCalInfo(jy);
        return gregorianToJDN(info.gy, 3, info.march) + (jm - 1) * 31 - intDiv(jm, 7) * (jm - 7) + jd - 1;
    }

    function jdnToJalali(jdn) {
        var gy = jdnToGregorian(jdn).gy;
        var jy = gy - 621;
        var info = jalaliCalInfo(jy);
        var jdn1f = gregorianToJDN(info.gy, 3, info.march);
        var k = jdn - jdn1f;
        var jm, jd;

        if (k >= 0) {
            if (k <= 185) {
                jm = 1 + intDiv(k, 31);
                jd = mod(k, 31) + 1;
                return { jy: jy, jm: jm, jd: jd };
            }
            k -= 186;
        } else {
            jy -= 1;
            k += 179;
            if (info.leap === 1) k += 1;
        }
        jm = 7 + intDiv(k, 30);
        jd = mod(k, 30) + 1;
        return { jy: jy, jm: jm, jd: jd };
    }

    function toJalali(gy, gm, gd) { return jdnToJalali(gregorianToJDN(gy, gm, gd)); }
    function toGregorian(jy, jm, jd) { return jdnToGregorian(jalaliToJDN(jy, jm, jd)); }
    function isLeapJalaliYear(jy) { return jalaliCalInfo(jy).leap === 0; }
    function jalaliMonthLength(jy, jm) {
        if (jm <= 6) return 31;
        if (jm <= 11) return 30;
        return isLeapJalaliYear(jy) ? 30 : 29;
    }

    // روز هفته جلالی: شنبه=0 ... جمعه=6 (بر پایه getDay میلادی که یکشنبه=0 است)
    function jalaliWeekday(jy, jm, jd) {
        var g = toGregorian(jy, jm, jd);
        var dow = new Date(g.gy, g.gm - 1, g.gd).getDay(); // 0=یکشنبه ... 6=شنبه
        return (dow + 1) % 7; // 0=شنبه ... 6=جمعه
    }

    // ---------------- ارقام و متن فارسی ----------------
    var FA_DIGITS = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];
    function toFaDigits(input) {
        return String(input).replace(/[0-9]/g, function (d) { return FA_DIGITS[+d]; });
    }
    function pad2(n) { return (n < 10 ? '0' : '') + n; }

    var MONTH_NAMES = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
    var WEEKDAY_SHORT = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج']; // شروع از شنبه

    // ---------------- خواندن/نوشتن مقدار میلادی استاندارد (سازگار با بک‌اند) ----------------
    function parseIso(value) {
        if (!value) return null;
        var m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(value);
        if (!m) return null;
        return {
            gy: +m[1], gm: +m[2], gd: +m[3],
            hh: m[4] !== undefined ? +m[4] : 0,
            mm: m[5] !== undefined ? +m[5] : 0,
        };
    }

    function formatIso(gy, gm, gd, hh, mm) {
        return gy + '-' + pad2(gm) + '-' + pad2(gd) + 'T' + pad2(hh) + ':' + pad2(mm);
    }

    // ============================================================
    // کلاس اصلی ویجت
    // ============================================================
    function JalaliDateTimePicker(hiddenInput, displayInput, options) {
        this.hiddenInput = hiddenInput;
        this.displayInput = displayInput;
        this.opts = Object.assign({ enableTime: true, onChange: function () {} }, options || {});

        this.selected = null;   // { jy, jm, jd, hh, mm } یا null
        this.viewJY = null;
        this.viewJM = null;
        this.mode = 'days';     // 'days' | 'months' | 'years'
        this.yearsPageStart = null;

        this._buildDom();
        this._bindEvents();
        this._initFromInput();
    }

    JalaliDateTimePicker.prototype._buildDom = function () {
        var root = document.createElement('div');
        root.className = 'jdp-popup';
        root.setAttribute('dir', 'rtl');

        root.innerHTML =
            '<div class="jdp-header">' +
                '<button type="button" class="jdp-nav jdp-nav-prev" aria-label="قبلی">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>' +
                '</button>' +
                '<div class="jdp-title">' +
                    '<button type="button" class="jdp-title-month" data-role="month-label"></button>' +
                    '<button type="button" class="jdp-title-year" data-role="year-label"></button>' +
                '</div>' +
                '<button type="button" class="jdp-nav jdp-nav-next" aria-label="بعدی">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>' +
                '</button>' +
            '</div>' +
            '<div class="jdp-weekdays" data-role="weekdays"></div>' +
            '<div class="jdp-grid" data-role="grid"></div>' +
            '<div class="jdp-time" data-role="time" hidden>' +
                '<div class="jdp-stepper" data-role="hour-stepper">' +
                    '<button type="button" class="jdp-stepper-btn" data-dir="up" aria-label="افزایش ساعت"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M5 15l7-7 7 7"/></svg></button>' +
                    '<div class="jdp-stepper-value" data-role="hour-value"></div>' +
                    '<button type="button" class="jdp-stepper-btn" data-dir="down" aria-label="کاهش ساعت"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M5 9l7 7 7-7"/></svg></button>' +
                '</div>' +
                '<div class="jdp-time-colon">:</div>' +
                '<div class="jdp-stepper" data-role="minute-stepper">' +
                    '<button type="button" class="jdp-stepper-btn" data-dir="up" aria-label="افزایش دقیقه"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M5 15l7-7 7 7"/></svg></button>' +
                    '<div class="jdp-stepper-value" data-role="minute-value"></div>' +
                    '<button type="button" class="jdp-stepper-btn" data-dir="down" aria-label="کاهش دقیقه"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M5 9l7 7 7-7"/></svg></button>' +
                '</div>' +
            '</div>' +
            '<div class="jdp-footer">' +
                '<button type="button" class="jdp-btn jdp-btn-ghost" data-role="clear">پاک کردن</button>' +
                '<button type="button" class="jdp-btn jdp-btn-ghost" data-role="today">امروز</button>' +
                '<button type="button" class="jdp-btn jdp-btn-primary" data-role="confirm">تأیید</button>' +
            '</div>';

        document.body.appendChild(root);
        this.root = root;
        this.el = {
            monthLabel: root.querySelector('[data-role="month-label"]'),
            yearLabel: root.querySelector('[data-role="year-label"]'),
            weekdays: root.querySelector('[data-role="weekdays"]'),
            grid: root.querySelector('[data-role="grid"]'),
            time: root.querySelector('[data-role="time"]'),
            hourValue: root.querySelector('[data-role="hour-value"]'),
            minuteValue: root.querySelector('[data-role="minute-value"]'),
            prev: root.querySelector('.jdp-nav-prev'),
            next: root.querySelector('.jdp-nav-next'),
            clearBtn: root.querySelector('[data-role="clear"]'),
            todayBtn: root.querySelector('[data-role="today"]'),
            confirmBtn: root.querySelector('[data-role="confirm"]'),
        };
        if (this.opts.enableTime) this.el.time.hidden = false;
    };

    JalaliDateTimePicker.prototype._bindEvents = function () {
        var self = this;

        this.displayInput.addEventListener('click', function () { self.toggle(); });
        this.displayInput.setAttribute('tabindex', '0');
        this.displayInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); self.toggle(); }
        });

        this.el.prev.addEventListener('click', function () { self._step(-1); });
        this.el.next.addEventListener('click', function () { self._step(1); });
        this.el.monthLabel.addEventListener('click', function () { self.mode = (self.mode === 'months') ? 'days' : 'months'; self._render(); });
        this.el.yearLabel.addEventListener('click', function () { self.mode = (self.mode === 'years') ? 'days' : 'years'; self.yearsPageStart = self.viewJY - (self.viewJY % 12); self._render(); });

        this.el.grid.addEventListener('click', function (e) {
            var cell = e.target.closest('[data-role="cell"]');
            if (!cell || cell.classList.contains('jdp-cell--disabled')) return;
            if (self.mode === 'days') {
                self.viewJM = self.viewJM; // بدون تغییر
                self._selectDay(+cell.dataset.jd);
            } else if (self.mode === 'months') {
                self.viewJM = +cell.dataset.jm;
                self.mode = 'days';
                self._render();
            } else if (self.mode === 'years') {
                self.viewJY = +cell.dataset.jy;
                self.mode = 'days';
                self._render();
            }
        });

        this.root.querySelectorAll('.jdp-stepper').forEach(function (stepper) {
            var isHour = stepper.dataset.role === 'hour-stepper';
            stepper.querySelectorAll('.jdp-stepper-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    if (!self.selected) {
                        self.selected = { jy: self.viewJY, jm: self.viewJM, jd: 1, hh: 0, mm: 0 };
                    }
                    var delta = btn.dataset.dir === 'up' ? 1 : -1;
                    if (isHour) self.selected.hh = mod(self.selected.hh + delta, 24);
                    else self.selected.mm = mod(self.selected.mm + delta, 60);
                    self._commit(true);
                    self._renderTime();
                });
            });
        });

        this.el.clearBtn.addEventListener('click', function () { self.clear(); self.close(); });
        this.el.todayBtn.addEventListener('click', function () {
            var now = new Date();
            var j = toJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
            self.viewJY = j.jy; self.viewJM = j.jm;
            self.selected = { jy: j.jy, jm: j.jm, jd: j.jd, hh: now.getHours(), mm: now.getMinutes() };
            self._commit(true);
            self._render();
        });
        this.el.confirmBtn.addEventListener('click', function () { self.close(); });

        document.addEventListener('mousedown', function (e) {
            if (!self.root.classList.contains('jdp-open')) return;
            if (self.root.contains(e.target) || e.target === self.displayInput) return;
            self.close();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && self.root.classList.contains('jdp-open')) self.close();
        });
        window.addEventListener('resize', function () { if (self.root.classList.contains('jdp-open')) self._position(); });
        window.addEventListener('scroll', function () { if (self.root.classList.contains('jdp-open')) self._position(); }, true);
    };

    JalaliDateTimePicker.prototype._initFromInput = function () {
        var parsed = parseIso(this.hiddenInput.value);
        var now = new Date();
        var todayJ = toJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
        if (parsed) {
            var j = toJalali(parsed.gy, parsed.gm, parsed.gd);
            this.selected = { jy: j.jy, jm: j.jm, jd: j.jd, hh: parsed.hh, mm: parsed.mm };
            this.viewJY = j.jy; this.viewJM = j.jm;
        } else {
            this.selected = null;
            this.viewJY = todayJ.jy; this.viewJM = todayJ.jm;
        }
        this._updateDisplay();
    };

    JalaliDateTimePicker.prototype._step = function (dir) {
        if (this.mode === 'days') {
            this.viewJM += dir;
            if (this.viewJM > 12) { this.viewJM = 1; this.viewJY += 1; }
            if (this.viewJM < 1) { this.viewJM = 12; this.viewJY -= 1; }
        } else if (this.mode === 'months') {
            this.viewJY += dir;
        } else if (this.mode === 'years') {
            this.yearsPageStart += dir * 12;
        }
        this._render();
    };

    JalaliDateTimePicker.prototype._selectDay = function (jd) {
        var hh = this.selected ? this.selected.hh : 0;
        var mm = this.selected ? this.selected.mm : 0;
        this.selected = { jy: this.viewJY, jm: this.viewJM, jd: jd, hh: hh, mm: mm };
        this._commit(true);
        this._render();
        if (!this.opts.enableTime) this.close();
    };

    JalaliDateTimePicker.prototype._commit = function (fireChange) {
        this._updateDisplay();
        if (fireChange) {
            var iso = this.getIsoValue();
            this.hiddenInput.value = iso || '';
            this.hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            this.hiddenInput.value = this.getIsoValue() || '';
        }
    };

    JalaliDateTimePicker.prototype.getIsoValue = function () {
        if (!this.selected) return null;
        var g = toGregorian(this.selected.jy, this.selected.jm, this.selected.jd);
        return formatIso(g.gy, g.gm, g.gd, this.selected.hh, this.selected.mm);
    };

    JalaliDateTimePicker.prototype._updateDisplay = function () {
        if (!this.selected) {
            this.displayInput.value = '';
            return;
        }
        var s = this.selected;
        var text = toFaDigits(s.jy) + '/' + toFaDigits(pad2(s.jm)) + '/' + toFaDigits(pad2(s.jd));
        if (this.opts.enableTime) text += ' - ' + toFaDigits(pad2(s.hh)) + ':' + toFaDigits(pad2(s.mm));
        this.displayInput.value = text;
    };

    JalaliDateTimePicker.prototype._renderTime = function () {
        if (!this.opts.enableTime) return;
        var s = this.selected || { hh: 0, mm: 0 };
        this.el.hourValue.textContent = toFaDigits(pad2(s.hh));
        this.el.minuteValue.textContent = toFaDigits(pad2(s.mm));
    };

    JalaliDateTimePicker.prototype._render = function () {
        this.el.monthLabel.textContent = MONTH_NAMES[this.viewJM - 1];
        this.el.yearLabel.textContent = toFaDigits(this.viewJY);
        this.el.monthLabel.classList.toggle('jdp-title-active', this.mode === 'months');
        this.el.yearLabel.classList.toggle('jdp-title-active', this.mode === 'years');

        if (this.mode === 'days') this._renderDays();
        else if (this.mode === 'months') this._renderMonths();
        else this._renderYears();

        this._renderTime();
    };

    JalaliDateTimePicker.prototype._renderDays = function () {
        this.el.weekdays.style.display = '';
        this.el.weekdays.innerHTML = WEEKDAY_SHORT.map(function (w) {
            return '<span class="jdp-weekday">' + w + '</span>';
        }).join('');

        var todayNow = new Date();
        var todayJ = toJalali(todayNow.getFullYear(), todayNow.getMonth() + 1, todayNow.getDate());
        var firstWeekday = jalaliWeekday(this.viewJY, this.viewJM, 1);
        var daysInMonth = jalaliMonthLength(this.viewJY, this.viewJM);

        var prevJY = this.viewJM === 1 ? this.viewJY - 1 : this.viewJY;
        var prevJM = this.viewJM === 1 ? 12 : this.viewJM - 1;
        var prevDaysInMonth = jalaliMonthLength(prevJY, prevJM);

        var html = '';
        var i, jd;

        for (i = 0; i < firstWeekday; i += 1) {
            jd = prevDaysInMonth - firstWeekday + 1 + i;
            html += '<button type="button" class="jdp-cell jdp-cell--muted" disabled>' + toFaDigits(jd) + '</button>';
        }
        for (jd = 1; jd <= daysInMonth; jd += 1) {
            var classes = ['jdp-cell'];
            if (this.selected && this.selected.jy === this.viewJY && this.selected.jm === this.viewJM && this.selected.jd === jd) classes.push('jdp-cell--selected');
            if (todayJ.jy === this.viewJY && todayJ.jm === this.viewJM && todayJ.jd === jd) classes.push('jdp-cell--today');
            html += '<button type="button" class="' + classes.join(' ') + '" data-role="cell" data-jd="' + jd + '">' + toFaDigits(jd) + '</button>';
        }
        var totalCells = firstWeekday + daysInMonth;
        var trailing = (7 - (totalCells % 7)) % 7;
        for (i = 1; i <= trailing; i += 1) {
            html += '<button type="button" class="jdp-cell jdp-cell--muted" disabled>' + toFaDigits(i) + '</button>';
        }

        this.el.grid.className = 'jdp-grid';
        this.el.grid.innerHTML = html;
    };

    JalaliDateTimePicker.prototype._renderMonths = function () {
        this.el.weekdays.style.display = 'none';
        this.el.weekdays.innerHTML = '';
        var self = this;
        var html = MONTH_NAMES.map(function (name, idx) {
            var jm = idx + 1;
            var cls = 'jdp-cell jdp-cell--month';
            if (self.viewJM === jm) cls += ' jdp-cell--selected';
            return '<button type="button" class="' + cls + '" data-role="cell" data-jm="' + jm + '">' + name + '</button>';
        }).join('');
        this.el.grid.className = 'jdp-grid jdp-grid--months';
        this.el.grid.innerHTML = html;
    };

    JalaliDateTimePicker.prototype._renderYears = function () {
        this.el.weekdays.style.display = 'none';
        this.el.weekdays.innerHTML = '';
        var start = this.yearsPageStart;
        var html = '';
        for (var i = 0; i < 12; i += 1) {
            var jy = start + i;
            var cls = 'jdp-cell jdp-cell--year';
            if (jy === this.viewJY) cls += ' jdp-cell--selected';
            html += '<button type="button" class="' + cls + '" data-role="cell" data-jy="' + jy + '">' + toFaDigits(jy) + '</button>';
        }
        this.el.grid.className = 'jdp-grid jdp-grid--years';
        this.el.grid.innerHTML = html;
        this.el.yearLabel.textContent = toFaDigits(start) + '–' + toFaDigits(start + 11);
    };

    JalaliDateTimePicker.prototype._position = function () {
        var rect = this.displayInput.getBoundingClientRect();
        var popupWidth = this.root.offsetWidth || 300;
        var left = rect.left + rect.width / 2 - popupWidth / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - popupWidth - 8));
        var top = rect.bottom + 8;
        if (top + this.root.offsetHeight > window.innerHeight - 8 && rect.top > this.root.offsetHeight) {
            top = rect.top - this.root.offsetHeight - 8;
        }
        this.root.style.left = left + 'px';
        this.root.style.top = top + 'px';
    };

    JalaliDateTimePicker.prototype.open = function () {
        this.mode = 'days';
        if (this.selected) { this.viewJY = this.selected.jy; this.viewJM = this.selected.jm; }
        this._render();
        this.root.classList.add('jdp-open');
        this._position();
    };

    JalaliDateTimePicker.prototype.close = function () {
        this.root.classList.remove('jdp-open');
    };

    JalaliDateTimePicker.prototype.toggle = function () {
        if (this.root.classList.contains('jdp-open')) this.close();
        else this.open();
    };

    JalaliDateTimePicker.prototype.clear = function () {
        this.selected = null;
        this._commit(true);
    };

    // مقداردهی برنامه‌نویسی (بدون شلیک onChange) — برای هماهنگ‌سازی با state بیرونی
    JalaliDateTimePicker.prototype.setValue = function (isoValue) {
        var parsed = parseIso(isoValue);
        if (parsed) {
            var j = toJalali(parsed.gy, parsed.gm, parsed.gd);
            this.selected = { jy: j.jy, jm: j.jm, jd: j.jd, hh: parsed.hh, mm: parsed.mm };
            this.viewJY = j.jy; this.viewJM = j.jm;
        } else {
            this.selected = null;
        }
        this.hiddenInput.value = isoValue || '';
        this._updateDisplay();
        if (this.root.classList.contains('jdp-open')) this._render();
    };

    global.JalaliDateTimePicker = JalaliDateTimePicker;
    global.jalaliDateUtil = {
        toJalali: toJalali, toGregorian: toGregorian,
        isLeapJalaliYear: isLeapJalaliYear, jalaliMonthLength: jalaliMonthLength,
        toFaDigits: toFaDigits,
    };
})(window);
