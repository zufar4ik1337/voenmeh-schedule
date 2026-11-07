"""
Обновление данных для сайта расписания Военмеха.

Что делает:
  1. Скачивает свежий XML с расписанием с voenmeh.ru (и сохраняет копию в data/source.xml).
     Если скачать не вышло — берёт прошлую копию data/source.xml.
  2. Собирает data/schedule.js — его читает index.html.
  3. Собирает календари для подписки:
       ics/g/<id группы>.ics
       ics/p/<id преподавателя>.ics

Запуск (из этой папки):
    python update_schedule.py                       # скачать с сайта вуза
    python update_schedule.py TimetableGroup50.xml  # взять уже скачанный файл

Нужен только Python 3.9+, ничего ставить не нужно.
"""

import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- настройки

LESSON_MIN = 90               # длительность пары, минут
SEMESTER_END = "2026-12-27"   # последний день занятий (в XML его нет)
SITE_URL = "https://zufar4ik1337.github.io/voenmeh-schedule/"   # адрес сайта — для ссылок из календаря

XML_URL = ("https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/"
           "_voenmeh_grafics/TimetableGroup50.xml")

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "data" / "source.xml"
OUT_JS = HERE / "data" / "schedule.js"
OUT_ICS = HERE / "ics"

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
TYPES = {"лек": "Лекция", "пр": "Практика", "лаб": "Лабораторная"}
MSK = timezone(timedelta(hours=3))   # Москва/Петербург, без перехода на летнее время


# ---------------------------------------------------------------- загрузка

def read_xml_text() -> str:
    if len(sys.argv) > 1:
        print(f"Читаю {sys.argv[1]}")
        text = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        try:
            print(f"Скачиваю {XML_URL}")
            req = urllib.request.Request(XML_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8")
        except Exception as e:
            if not SOURCE.exists():
                raise SystemExit(f"Не удалось скачать XML ({e}), и прошлой копии нет.")
            print(f"Не удалось скачать ({e}). Беру прошлую копию {SOURCE.name}")
            return SOURCE.read_text(encoding="utf-8")
    text = text[text.index("<"):]   # если файл сохранён из браузера, сверху бывает лишняя строка
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE.write_text(text, encoding="utf-8")
    return text


def clean(text) -> str:
    return (text or "").strip().strip(";").strip()


# ---------------------------------------------------------------- разбор

def parse(text: str) -> dict:
    root = ET.fromstring(text)
    period = root.find("Period")
    start = date(int(period.get("StartYear")), int(period.get("StartMonth")), int(period.get("StartDay")))

    lecturers, groups = {}, []
    for g in root.findall("Group"):
        lessons = []
        for l in g.findall("Days/Day/GroupLessons/Lesson"):
            disc = clean(l.findtext("Discipline"))
            kind, _, name = disc.partition(" ")
            if kind not in TYPES:
                kind, name = "", disc
            h, m = map(int, clean(l.findtext("Time")).split()[0].split(":"))
            ids = []
            for lec in l.findall("Lecturers/Lecturer"):
                lid = clean(lec.findtext("IdLecturer"))
                lecturers[lid] = clean(lec.findtext("ShortName"))
                ids.append(lid)
            lessons.append({
                "d": DAYS.index(clean(l.findtext("DayTitle"))),  # день недели, 0 = пн
                "w": int(l.findtext("WeekCode")),                # 1 нечётная, 2 чётная
                "t": f"{h:02d}:{m:02d}",                         # начало пары
                "k": kind,                                       # лек / пр / лаб
                "s": name,                                       # дисциплина
                "p": ids,                                        # id преподавателей
                "r": clean(l.findtext("Classroom")),             # аудитория
            })
        if lessons:
            lessons.sort(key=lambda x: (x["d"], x["t"], x["w"]))
            groups.append({"n": g.get("Number"), "id": g.get("IdGroup"), "l": lessons})

    return {
        "title": period.get("Title"),
        "start": start.isoformat(),
        "weekCount": int(root.find("Weeks").get("WeekCount")),
        "lessonMin": LESSON_MIN,
        "semesterEnd": SEMESTER_END,
        "siteUrl": SITE_URL,
        "updated": datetime.now(MSK).strftime("%d.%m.%Y %H:%M"),
        "groups": groups,
        "lecturers": lecturers,
    }


def lecturer_lessons(data: dict) -> dict:
    """id преподавателя -> его пары (одна пара у нескольких групп = одна запись)."""
    res = {}
    for g in data["groups"]:
        for l in g["l"]:
            for pid in l["p"]:
                slots = res.setdefault(pid, {})
                key = (l["d"], l["w"], l["t"], l["s"], l["r"])
                slots.setdefault(key, {**l, "groups": []})["groups"].append(g["n"])
    return {pid: sorted(s.values(), key=lambda x: (x["d"], x["t"], x["w"])) for pid, s in res.items()}


# ---------------------------------------------------------------- календари .ics

def ics_esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line: str) -> str:
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" "
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def utc(d: date, h: int, m: int) -> str:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=MSK).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def site_link(kind: str, key: str) -> str:
    """Ссылка из календаря обратно на сайт (utm — чтобы Метрика видела переходы из календаря)."""
    return f"{SITE_URL}?utm_source=calendar&utm_medium=ics#{kind}={urllib.parse.quote(key)}"


def build_ics(title: str, uid_prefix: str, lessons: list, data: dict, lecturers: dict, link: str = "") -> str:
    sem_start = date.fromisoformat(data["start"])
    monday0 = sem_start - timedelta(days=sem_start.weekday())
    until = utc(date.fromisoformat(SEMESTER_END), 23, 59)
    wc = data["weekCount"]

    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//voenmeh-schedule//RU", "CALSCALE:GREGORIAN",
         "METHOD:PUBLISH", f"X-WR-CALNAME:Расписание {title}", "X-WR-TIMEZONE:Europe/Moscow",
         "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]   # как часто календарю обновляться
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for l in lessons:
        first = monday0 + timedelta(weeks=l["w"] - 1, days=l["d"])
        while first < sem_start:
            first += timedelta(weeks=wc)
        h, m = map(int, l["t"].split(":"))
        end_min = h * 60 + m + LESSON_MIN
        who = (", ".join(lecturers.get(p, "") for p in l["p"]) if "groups" not in l
               else "Группы: " + ", ".join(l["groups"]))
        uid = "-".join([uid_prefix, str(l["d"]), str(l["w"]), l["t"], l["s"]])
        summary = l["s"] + (" (" + l["k"] + ")" if l["k"] else "")
        L += ["BEGIN:VEVENT",
              f"UID:{ics_esc(uid)}@voenmeh-schedule",
              f"DTSTAMP:{stamp}",
              f"DTSTART:{utc(first, h, m)}",
              f"DTEND:{utc(first, end_min // 60, end_min % 60)}",
              f"RRULE:FREQ=WEEKLY;INTERVAL={wc};UNTIL={until}",
              f"SUMMARY:{ics_esc(summary)}"]
        if l["r"]:
            L.append(f"LOCATION:{ics_esc('Ауд. ' + l['r'])}")
        desc = "\n".join(x for x in [TYPES.get(l["k"], ""), who] if x)
        if link:
            desc += f"\n\nЗадачи и заметки по предмету — на сайте: {link}"
            L.append(f"URL:{link}")
        if desc:
            L.append(f"DESCRIPTION:{ics_esc(desc)}")
        L.append("END:VEVENT")
    L.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(x) for x in L) + "\r\n"


# ---------------------------------------------------------------- main

def main():
    data = parse(read_xml_text())

    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    # .js, а не .json — чтобы index.html открывался двойным кликом, без сервера
    OUT_JS.write_text("window.SCHEDULE = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
                      encoding="utf-8")

    (OUT_ICS / "g").mkdir(parents=True, exist_ok=True)
    (OUT_ICS / "p").mkdir(parents=True, exist_ok=True)
    for g in data["groups"]:
        (OUT_ICS / "g" / f"{g['id']}.ics").write_text(
            build_ics(g["n"], f"g{g['id']}", g["l"], data, data["lecturers"], site_link("g", g["n"])),
            encoding="utf-8", newline="")
    by_lecturer = lecturer_lessons(data)
    for pid, lessons in by_lecturer.items():
        (OUT_ICS / "p" / f"{pid}.ics").write_text(
            build_ics(data["lecturers"][pid], f"p{pid}", lessons, data, data["lecturers"], site_link("p", pid)),
            encoding="utf-8", newline="")

    print(f"{data['title']}: групп {len(data['groups'])}, преподавателей {len(by_lecturer)}")
    print(f"Готово: {OUT_JS.name} и календари в папке {OUT_ICS.name}/")


if __name__ == "__main__":
    main()
