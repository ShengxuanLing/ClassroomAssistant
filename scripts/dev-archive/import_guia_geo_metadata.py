# -*- coding: utf-8 -*-
"""一次性导入: 「Bases per a la Geoinformació」的 guia docent -> 课程 metadata。

运行方式 (项目根目录):
    Python/pythoncore-3.14-64/python.exe scripts/dev-archive/import_guia_geo_metadata.py

设计约束:
- 只写课程 metadata, 走与 teacher/semester 完全相同的存储路径
  (PATCH /api/courses/{id} -> update_course -> _flush_course), 零 schema 迁移。
- 幂等: 重复运行结果一致 (metadata 用逐键覆盖, 不整包替换)。
- 原文逐字摘自 PDF, 不做改写; 本脚本不翻译任何 guia docent 内容。
- 课程页 UI (views/courses.js) 按 `guia_*` 前缀的 metadata 键渲染,
  其余课程没有这些键时该卡片不渲染 —— 零影响。

注意: 必须在服务**运行中**执行 —— 数据真相在运行进程的内存 + WAL 数据库,
直连 SQLite 写入会被运行进程的内存态在下次落盘时覆盖 (AGENTS.md 2026-09-21
的踩坑记录: 写入必须两边都改; 走 API 就是两边都改的唯一入口)。
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"
COURSE_ID = "course-3fd6392d1fd78e87"  # Bases per a la Geoinformació (GEO)

# ---- 原文逐字摘自 guia docent PDF (2026/2027, 7 页) -------------------------

GUIA: dict = {
    "course_code": "106934",
    "credits": "6",
    "degree": "Gestió de Ciutats Intel·ligents i Sostenibles",
    "degree_type": "FB",
    "year": "2",
    "academic_year": "2026/2027",
    "contact_email": "marc.castello.bueno@uab.cat",
    "contact_name": "Marc Castelló Bueno",
    "teaching_team": [
        "Miquel Àngel Vargas Garcia",
        "Magda Pla Montferrer",
    ],
    "prerequisites": (
        "No hi ha prerequisits vinculats a aquesta assignatura, tot i que serà de "
        "gran utilitat tenir habilitats informàtiques sobretot d'ofimàtica i "
        "estadística."
    ),
    "objectives": [
        (
            "La matèria proporcionarà els elements necessaris per adquirir i "
            "entendre les concepcions cartogràfiques necessàries per a la "
            "representació espacial de les dinàmiques territorials."
        ),
        (
            "Una ciutat intel·ligent pretén oferir als habitants que hi resideixen "
            "una alta qualitat de vida consumint els mínims recursos possibles "
            "gràcies a la gestió de la ciutat amb les noves tecnologies. Ara bé, "
            "per a representar les dinàmiques urbanes i analitzar la ciutat és "
            "bàsic adquirir i entendre les concepcions cartogràfiques necessàries "
            "per a la representació espacial."
        ),
    ],
    "learning_outcomes": [
        {
            "code": "CM09",
            "text": (
                "Relacionar els coneixements i les habilitats en geomàtica amb els "
                "aportats per altres tècnics en equips interdisciplinaris."
            ),
        },
        {
            "code": "KM14",
            "text": (
                "Aplicar les convencions cartogràfiques que permetin un disseny "
                "apropiat dels mapes com a mitjà de transmissió d'informació."
            ),
        },
        {
            "code": "SM13",
            "text": (
                "Desenvolupar plataformes de gestió, integració de serveis al "
                "ciutadà i governança basades en l'ús de la geoinformació."
            ),
        },
    ],
    "syllabus": [
        {
            "title": "Bloc 1. Introducció a la cartografia",
            "items": [
                "Conceptes bàsics de cartografia",
                "Història de la cartografia",
                "El mapa: elements bàsics, tipus i funcions",
                "El mapa digital i els Sistemes d'Informació Geogràfica",
            ],
        },
        {
            "title": (
                "Bloc 2. Principis de representació geoespacial: punts, línies i "
                "polígons"
            ),
            "items": [
                "El concepte de capa",
                (
                    "La informació geogràfica: tipus, components i característiques"
                ),
                "Dades vectorials i els seus formats",
                "Dades ràsters i els seus formats",
            ],
        },
        {
            "title": "Bloc 3. Escales territorials i les seves funcions",
            "items": [
                "El concepte d'escala",
                "Principis bàsics en topografia",
                "Orientació",
                "La representació del relleu",
            ],
        },
        {
            "title": "Bloc 4. Projeccions cartogràfiques i les seves funcions",
            "items": [
                "El concepte de projecció cartogràfica",
                "La projecció UTM",
                (
                    "La georeferenciació absoluta, la relativa i la geocodificació "
                    "per adreces"
                ),
            ],
        },
        {
            "title": "Bloc 5. Simbolització de la informació i disseny gràfic",
            "items": [
                "Les variables visuals",
                "La simbolització en punts, línies i polígons",
                (
                    "El disseny gràfic: principis bàsics i composició cartogràfica"
                ),
            ],
        },
        {
            "title": "Bloc 6. Principals fonts cartogràfiques",
            "items": [
                (
                    "Fonts de dades alfanumèriques a nivell mundial, europeu, "
                    "espanyol, català i a l'àmbit local"
                ),
                (
                    "Fonts de dades espacials a nivell mundial, europeu, espanyol, "
                    "català i a l'àmbit local"
                ),
            ],
        },
    ],
    "syllabus_note": (
        "El cronograma, amb la seqüenciació del temari i les activitats "
        "avaluatives, es pujarà al campus virtual a l'inici de l'assignatura."
    ),
    "teaching_hours": [
        {"title": "Realització de pràctiques", "hours": "25", "ects": "1"},
        {"title": "Classes magistrals", "hours": "20", "ects": "0,8"},
        {
            "title": "Realització de pràctiques, activitats i estudi personal",
            "hours": "43",
            "ects": "1,72",
        },
    ],
    "assessment_items": [
        {
            "title": "Realització autònoma de pràctiques",
            "weight": "20",
        },
        {
            "title": "Pràctiques setmanals",
            "weight": "10",
        },
        {
            "title": (
                "Treball final. Composició cartogràfica i cerca de fonts de dades"
            ),
            "weight": "30",
        },
        {
            "title": "Exàmens teòrics i pràctics",
            "weight": "40",
        },
    ],
    "assessment_note": (
        "IMPORTANT: Aquesta assignatura/mòdul no preveu el sistema d'avaluació "
        "única."
    ),
    "assessment_items_detail": [
        "2 Exàmens teòrics-pràctics parcials (20% un a mig semestre-20% al final del semestre)",
        "Exercicis pràctics (20%)",
        "Entrega de pràctiques setmanals (10%)",
        "Treball final (30%): Elaboració d'un mapa temàtic urbà",
    ],
    "assessment_requirements": (
        "L'avaluació de l'aprenentatge es basa en els resultats de les pràctiques "
        "realitzats de forma autònoma. Aquestes pràctiques han de ser lliurades, "
        "almenys el 80% d'elles, en el termini estipulat pel professor. L'estudiant "
        "no es podrà presentar a l'examen si no ha presentat les pràctiques "
        "demanades fins aquell moment i la nota final serà d'un No Avaluable. Es "
        "realitzarà un seguiment de l'assistència de l'alumnat tant a les classes "
        "teòriques com a les pràctiques. Per poder ser avaluat, l'alumne haurà "
        "d'haver assistit, com a mínim, al 80% de les sessions programades. En cas "
        "contrari, la qualificació final serà \"No avaluable\". D'altra banda, els "
        "estudiants que hagin superat l'assignatura rebran una bonificació del 5% "
        "en la nota final si han complert amb el requisit mínim d'assistència."
    ),
    "pass_requirements": (
        "Els exàmens teòrics-pràctics constaran d'una nota per la part teòrica un "
        "una per la part pràctica i s'avaluen per separat. Les mitjanes entre les "
        "dues proves teòriques o pràctiques de l'examen es fa a partir de la nota "
        "de 4 i només se superaran els exàmens si la mitjana de les qualificacions "
        "és d'un mínim de 5. Tanmateix, la part teòrica i practica de l'assignatura "
        "s'ha d'aprovar per separar amb una nota mitjana d'un mínim de 5 per tal de "
        "què facin mitjana. Com a exemple, si un alumne treu un 6 i un 4 de la part "
        "teòrica en els dos exàmens i un 5 i un 4 de la part pràctica, l'estudiant "
        "no haurà assolits els conceptes mínims necessaris de l'assignatura ja que "
        "la mitjana entre les dues parts és de 5 i 4.5, havent-se d'aprovar les "
        "dues parts amb una nota mínima de 5. En aquest cas podrà optar a la "
        "recuperació d'una o de les dues parts."
    ),
    "recovery": (
        "Un cop acabada l'avaluació ordinària, si l'alumne/a ha suspès tindrà la "
        "possibilitat de realitzar un examen de reavaluació dins de les dates que "
        "programi la Facultat, amb les mateixes condicions que per a realitzar "
        "l'examen ordinari: Haver lliurat, com a mínim, el 80% dels treballs "
        "pràctics. Per aprovar l'assignatura caldrà aprovar l'examen de "
        "reavaluació si no s'ha aprovat l'examen ordinari. Es podrà recuperar els "
        "exàmens teòrics i pràctics. Les pràctiques suspeses aixi com el treball "
        "final es podran recuperaran si s'ha presentat l'activitat i aquesta "
        "recuperació no podrà superar una puntuació de 6."
    ),
    "ai_policy": (
        "En aquesta assignatura es permet l'ús de tecnologies d'Intel·ligència "
        "Artificial (IA) com a part integrant del desenvolupament del treball, "
        "sempre que el resultat final reflecteixi una contribució significativa de "
        "l'estudiant en l'anàlisi i la reflexió personal. L'estudiant haurà de: "
        "(i) identificar quines parts han estat generades amb IA; (ii) especificar "
        "les eines utilitzades; i (iii) incloure una reflexió crítica sobre com "
        "aquestes han influït el procés i el resultat final de l'activitat. La no "
        "transparència de l'ús de la IA en aquesta activitat avaluable es "
        "considerarà falta d'honestedat acadèmica i comporta que l'activitat "
        "s'avaluï amb un 0 i no espugui recuperar, o sancions majors en casos de "
        "gravetat."
    ),
    "software": (
        "Per a la realització de l'assignatura es compta amb un programari "
        "específic de SIG: MiraMon (lliure per estudiants)."
    ),
    "groups_note": (
        "La informació proporcionada és provisional fins al 30 de novembre."
    ),
    "groups": [
        {
            "kind": "TE",
            "group": "61",
            "language": "Català",
            "semester": "primer quadrimestre",
            "shift": "tarda",
        },
        {
            "kind": "PAUL",
            "group": "611",
            "language": "Català",
            "semester": "primer quadrimestre",
            "shift": "tarda",
        },
        {
            "kind": "PLAB",
            "group": "611",
            "language": "Català",
            "semester": "primer quadrimestre",
            "shift": "tarda",
        },
        {
            "kind": "PLAB",
            "group": "612",
            "language": "Català",
            "semester": "primer quadrimestre",
            "shift": "tarda",
        },
    ],
    # 可追溯性: 内容来自哪份文件。不写路径里的盘符, 只写文件名 + 提取日期。
    "source": "Bases per a la Geoinformació.pdf (guia docent 2026/2027, extret 2026-09-22)",
}


def main() -> int:
    # 1. 读当前课程 (拿已有 metadata, 做合并而不是覆盖)
    with urllib.request.urlopen(f"{BASE}/api/courses/{COURSE_ID}", timeout=10) as resp:
        current = json.load(resp)["data"]
    merged = dict(current.get("metadata") or {})
    merged.update({f"guia_{key}": value for key, value in GUIA.items()})

    # 2. PATCH 写回 (走运行中的服务 -> 内存态与数据库两边都改)
    req = urllib.request.Request(
        f"{BASE}/api/courses/{COURSE_ID}",
        data=json.dumps({"metadata": merged}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        updated = json.load(resp)["data"]

    # 3. 读回验证
    got = {k: v for k, v in updated.get("metadata", {}).items() if k.startswith("guia_")}
    expected_keys = {f"guia_{key}" for key in GUIA}
    missing = expected_keys - set(got)
    print(f"guia_* keys written: {len(got)} / {len(GUIA)}")
    if missing:
        print("MISSING:", sorted(missing))
        return 1
    print("OK: guia docent metadata written for", COURSE_ID)
    return 0


if __name__ == "__main__":
    sys.exit(main())
