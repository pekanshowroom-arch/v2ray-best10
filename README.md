# اسکنر کانفیگ V2Ray برای PassWall 2

این پروژه اشتراک عمومی
[V2RayCFGDumper](https://github.com/miladtahanian/V2RayCFGDumper)
را دانلود می‌کند، تک‌تک کانفیگ‌ها را با **هستهٔ واقعی Xray** از روی اینترنت همین کامپیوتر تست می‌کند،
۱۰ کانفیگ برتر را انتخاب می‌کند و در یک مخزن GitHub منتشر می‌کند.

هدف این است که روتر فقط همان ۱۰ لینک سبک را دریافت کند و اسکن سنگین روی کامپیوتر انجام شود.

---

## خروجی‌ها

| فایل | کاربرد |
|---|---|
| `output/sub_base64.txt` | اشتراک Base64 — **پیشنهادی برای PassWall 2** |
| `output/sub.txt` | لینک‌های ساده، هر کدام در یک خط |
| `output/best10.json` | اطلاعات کامل تست‌ها (تأخیر، سرعت، امتیاز) |
| `output/README.md` | جدول وضعیت آخرین اسکن |

نام هر کانفیگ بعد از تست به شکل زیر بازنویسی می‌شود:

```text
01 | VLESS | 420ms | 12.4Mbps | نام اصلی
```

## آدرس اشتراک آماده

این آدرس همین حالا فعال است و در PassWall 2 قابل استفاده است:

```text
https://raw.githubusercontent.com/pekanshowroom-arch/v2ray-best10/main/output/sub_base64.txt
```

اگر نام کاربری یا نام مخزن را تغییر دادید، الگوی کلی این است:

```text
https://raw.githubusercontent.com/<USER>/<REPO>/main/output/sub_base64.txt
```

---

## پیش‌نیازها

- Windows 10 یا 11
- Python **3.11 یا جدیدتر**
- Git
- GitHub CLI (`gh`)
- اینترنت در زمان اسکن
- کامپیوتر در زمان اجرای اسکن روشن باشد (یا اسکن بعدی هنگام روشن شدن انجام شود)

نصب سریع پیش‌نیازهای ویندوز:

```powershell
winget install Python.Python.3.12
winget install Git.Git
winget install GitHub.cli
```

بعد از نصب، یک ترمینال جدید باز کنید تا `python` و `gh` در PATH شناخته شوند.

---

## نصب

در پوشهٔ پروژه، PowerShell را باز کنید و اجرا کنید:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

این اسکریپت به‌صورت خودکار:

1. محیط مجازی `.venv` می‌سازد.
2. کتابخانهٔ `httpx[socks]` را نصب می‌کند.
3. Xray-core نسخهٔ `v25.12.8` را در `bin/xray.exe` قرار می‌دهد.
4. یک اسکن اولیه انجام می‌دهد (با `-SkipScan` می‌توانید این مرحله را رد کنید).

> چرا نسخهٔ ۲۵؟ چون نسخهٔ ۲۶ گزینهٔ `allowInsecure` را حذف کرده و بخش زیادی از کانفیگ‌های عمومی به آن وابسته‌اند.

اگر دانلود مستقیم GitHub در شبکهٔ شما قطع می‌شود، `bootstrap.py` به‌طور خودکار از آینه‌های
`ghproxy.net` و `gh-proxy.com` استفاده می‌کند. برای دانلود مجدد:

```powershell
.\.venv\Scripts\python.exe bootstrap.py --force
```

---

## اتصال به GitHub و ساخت مخزن

یک‌بار وارد GitHub شوید:

```powershell
gh auth login --hostname github.com --git-protocol https --web
```

سپس مخزن عمومی بسازید و خروجی را push کنید:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_repo.ps1
```

نام پیش‌فرض مخزن `v2ray-best10` است. برای تغییر نام:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_repo.ps1 -RepoName my-v2ray-configs
```

در پایان، آدرس خام `sub_base64.txt` را چاپ می‌کند؛ همان را در PassWall 2 وارد کنید.

> مخزن باید **عمومی** باشد. PassWall نمی‌تواند توکن GitHub را برای دریافت فایل خام بفرستد.
> پس همهٔ ۱۰ کانفیگ برای همه قابل مشاهده خواهند بود. کانفیگ خصوصی یا حساس در این پروژه قرار ندهید.

---

## زمان‌بندی خودکار

برای اجرای خودکار هر ۶ ساعت:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -RunNow
```

- زمان‌های اجرا: `00:00`، `06:00`، `12:00`، `18:00`
- اگر کامپیوتر در آن زمان خاموش باشد، اسکن هنگام روشن شدن بعدی انجام می‌شود.
- تسک با اکانت کاربر فعلی و فقط وقتی کاربر logged on باشد اجرا می‌شود.

بررسی وضعیت:

```powershell
Get-ScheduledTaskInfo -TaskName ConfigScanner
Start-ScheduledTask -TaskName ConfigScanner
```

حذف تسک:

```powershell
Unregister-ScheduledTask -TaskName ConfigScanner -Confirm:$false
```

برای تغییر بازه:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -IntervalHours 12 -RunNow
```

بازه باید یکی از `1, 2, 3, 4, 6, 8, 12, 24` باشد.

---

## اجرای دستی

ساده‌ترین راه استفاده از `run.cmd` است (نیازی به تغییر Execution Policy ندارد):

```text
run.cmd                    # اسکن کامل + انتشار در GitHub
run.cmd --no-push          # اسکن و نوشتن فایل‌ها، بدون push
run.cmd --limit 20         # فقط ۲۰ کانفیگ اول (تست سریع)
run.cmd --top 10 -v        # ۱۰ کانفیگ برتر + لاگ کامل
```

اگر مستقیم با PowerShell اجرا می‌کنید، دسترسی اجرای اسکریپت را موقتاً باز کنید:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 --no-push
```

گزینه‌های مفید دیگر:

```text
--source <URL>        تغییر آدرس اشتراک مبدأ
--protocols vless,vmess,trojan,ss
--concurrency 8       تعداد تست هم‌زمان
--xray <PATH>         مسیر دیگر برای Xray
--no-prefilter        رد کردن فیلتر سریع TCP
--keep-runtime        نگه‌داشتن فایل‌های موقت Xray
```

لاگ هر اجرا در `logs/scan-YYYYMMDD-HHMMSS.log` ذخیره می‌شود.

---

## تنظیمات

فایل `config.json` را می‌توانید ویرایش کنید. مهم‌ترین کلیدها:

| کلید | پیش‌فرض | توضیح |
|---|---:|---|
| `top_n` | `10` | تعداد کانفیگ نهایی |
| `max_per_host` | `2` | حداکثر کانفیگ از هر سرور برای تنوع |
| `min_speed_mbps` | `0.3` | حداقل سرعت مطلوب برای اولویت‌دهی |
| `concurrency` | `10` | تعداد تست هم‌زمان Xray |
| `speed_timeout` | `12` | حداکثر زمان تست سرعت (ثانیه) |
| `speed_bytes` | `2000000` | حجم دانلود تست سرعت |
| `prefilter` | `true` | فیلتر سریع TCP قبل از اجرای Xray |
| `git.enabled` | `true` | انتشار خودکار در GitHub |

مقدار `min_speed_mbps` فقط **اولویت** است؛ اگر تعداد کانفیگ‌های سریع کم باشد، از کانفیگ‌های
کندتر هم برای پر کردن ۱۰ جای خالی استفاده می‌شود.

---

## روش انتخاب کانفیگ‌ها

1. اشتراک مبدأ دانلود و خط‌به‌خط تجزیه می‌شود.
2. کانفیگ‌های مخدوش (مثل Base64 چسبیده به ابتدای لینک) هم بازیابی می‌شوند.
3. آدرس‌های خصوصی/لوپ‌بک، پروتکل‌های پشتیبانی‌نشده و تکراری‌ها حذف می‌شوند.
4. یک فیلتر سریع TCP انجام می‌شود.
5. هر کانفیگ در یک پروسهٔ کوتاه‌عمر Xray با inbound محلی SOCKS تست می‌شود:
   - تأخیر: `https://www.gstatic.com/generate_204` و جایگزین Cloudflare
   - سرعت: دانلود ۲ مگابایت از `speed.cloudflare.com` و جایگزین Cachefly
6. امتیاز نهایی: **۸۵٪ سرعت + ۱۵٪ تأخیر**
7. حداکثر ۲ کانفیگ از هر هاست انتخاب می‌شود تا تنوع حفظ شود.

همهٔ تست‌ها از اینترنت همین کامپیوتر انجام می‌شوند؛ بنابراین نتیجه با شرایط شبکهٔ واقعی شما
همخوانی بیشتری دارد تا تست از سرور GitHub Actions.

---

## تنظیم PassWall 2

1. این آدرس را کپی کنید:

   ```text
   https://raw.githubusercontent.com/pekanshowroom-arch/v2ray-best10/main/output/sub_base64.txt
   ```

2. در PassWall 2 به بخش **Node Subscription / اشتراک** بروید.
3. آدرس را اضافه کنید و نوع اشتراک را در صورت وجود **Base64** انتخاب کنید.
4. دکمهٔ **Update / به‌روزرسانی** را بزنید.
5. در صورت نیاز، به‌روزرسانی خودکار اشتراک در روتر را روی ۶ یا ۱۲ ساعت بگذارید.

اگر PassWall لینک Base64 را نپذیرفت، از `output/sub.txt` استفاده کنید.

> با هر اسکن، محتوای فایل عوض می‌شود. روتر فقط همان فایل کوچک را می‌گیرد و هیچ اسکنی روی روتر انجام نمی‌شود.

---

## عیب‌یابی

### `gh` وارد نشده است

```powershell
gh auth login --hostname github.com --git-protocol https --web
```

### هیچ کانفیگی تست را پاس نکرد

- اتصال اینترنت و دسترسی به `raw.githubusercontent.com` را بررسی کنید.
- اسکن سریع با لاگ کامل:

  ```text
  run.cmd --limit 30 -v
  ```

- در `config.json` مقدار `latency_timeout` را موقتاً بیشتر کنید (مثلاً `10`).
- اگر منبع اصلی خراب/خالی است، آدرس دیگری با `--source` بدهید.

### خطای دانلود Xray

```powershell
.\.venv\Scripts\python.exe bootstrap.py --force
```

### تسک زمان‌بندی اجرا نمی‌شود

- کامپیوتر باید روشن و کاربر logged on باشد.
- خروجی این دستور را بررسی کنید:

  ```powershell
  Get-ScheduledTaskInfo -TaskName ConfigScanner
  ```

- یک‌بار دستی اجرا کنید:

  ```powershell
  Start-ScheduledTask -TaskName ConfigScanner
  ```

- لاگ‌های `logs/` را ببینید.

### لینک خام 404 می‌دهد

- مخزن باید Public باشد.
- نام کاربری/مخزن و شاخهٔ `main` را بررسی کنید.
- مسیر باید به `output/sub_base64.txt` ختم شود.

### سرعت‌ها پایین‌اند

سرعت کانفیگ‌های عمومی معمولاً نوسان دارد. چند نکته:

- اسکن را در ساعتی اجرا کنید که شبکهٔ شما خلوت است.
- `concurrency` را کمی کم کنید تا تست‌ها روی پهنای باند هم رقابت نکنند.
- `speed_bytes` را به `3000000` افزایش دهید (تست دقیق‌تر، اما کندتر).
- اگر تعداد کانفیگ‌های سریع کم است، `min_speed_mbps` را پایین‌تر بیاورید.

---

## ساختار پروژه

```text
bootstrap.py        دانلود و نصب Xray-core
subscription.py     دانلود و تجزیهٔ اشتراک (شامل Base64های چسبیده)
config_parser.py    تبدیل لینک‌ها به outbound JSON و اعتبارسنجی
tester.py           فیلتر TCP + تست واقعی latency/speed با Xray
scanner.py          خط اصلی: دانلود، تست، رتبه‌بندی، خروجی، انتشار
publisher.py        تولید فایل‌های اشتراک و commit/push به Git
config.json          تنظیمات
setup.ps1           نصب اولیه
run.ps1 / run.cmd   اجرای دستی
setup_repo.ps1      ساخت مخزن GitHub و push اولیه
install_task.ps1    ثبت تسک زمان‌بندی ویندوز
```

---

## نکات امنیتی

- کانفیگ‌ها از منبع عمومی شخص ثالث می‌آیند و قابل اعتماد نیستند.
- مخزن GitHub عمومی است؛ آن را فقط برای همین اشتراک نگه دارید.
- اسکنر فقط از Xray رسمی استفاده می‌کند و کانفیگ‌ها را از شبکهٔ شما عبور می‌دهد.
- فایل‌های `bin/`، `.venv/`، `logs/` و `runtime/` در Git نادیده گرفته می‌شوند.

---

## اعتبار

- منبع کانفیگ‌ها: [miladtahanian/V2RayCFGDumper](https://github.com/miladtahanian/V2RayCFGDumper)
- هستهٔ تست: [XTLS/Xray-core](https://github.com/XTLS/Xray-core)
