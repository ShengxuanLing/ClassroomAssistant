# -*- coding: utf-8 -*-
"""一次性导入: 剩余四门课的 guia docent -> 课程 metadata (与 GEO 同 schema)。

运行方式 (项目根目录, 服务必须**运行中** —— 走 PATCH API 让内存态和数据库两边都改):
    Python/pythoncore-3.14-64/python.exe scripts/dev-archive/import_guia_batch_metadata.py

设计约束:
- 原文逐字摘自各 guia docent PDF (2026/2027), 不改写不翻译。
- 幂等: metadata 逐键覆盖, 重复运行结果一致。
- schema 与 GEO 完全一致 (scripts/dev-archive/import_guia_geo_metadata.py),
  渲染端 src/web/views/courses.js 按 guia_* 前缀键消费。
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"

COURSES = {
    "course-bd4f98777c97a0d2": "PA",
    "course-32dde014868219be": "GP",
    "course-680f27ea0e304d6d": "GA",
    "course-6ea554f295431036": "MC",
}

# ============================================================================
# PA — Programació d'Aplicacions a Internet.pdf (5 页, 原文逐字)
# ============================================================================
GUIA_PA: dict = {
    "course_code": "106937",
    "credits": "6",
    "degree": "Gestió de Ciutats Intel·ligents i Sostenibles",
    "degree_type": "OB",
    "year": "2",
    "academic_year": "2026/2027",
    "contact_email": "marc.esteve.garcia@uab.cat",
    "contact_name": "Marc Esteve García",
    "teaching_team": [],
    "prerequisites": (
        "Cal haver cursat les assignatures Informàtica i Programació del primer curs."
    ),
    "objectives": [
        "Comprendre les diferències entre HTML, CSS i JavaScript i saber fer pàgines web que facin servir correctament aquestes tres tecnologies.",
        "Entendre la complexitat de la creació d'aplicacions per a la web, així com les parts que composen qualsevol desenvolupament web.",
        "Dominar els aspectes bàsics de la programació d'aplicacions.",
        "Saber interpretar i descomposar un problema informàtic per tal de poder programar una solució.",
        "Saber crear petites aplicacions web que interaccionin amb l'usuari mitjançant formularis.",
    ],
    "learning_outcomes": [
        {"code": "CM05", "text": "Relacionar els coneixements i les habilitats informàtiques amb els aportats per altres tècnics en equips interdisciplinaris."},
        {"code": "KM09", "text": "Entendre el funcionament i la gestió correcta de les bases de dades."},
        {"code": "SM08", "text": "Utilitzar les tècniques d'anàlisi d'algorismes i programa per a dissenyar noves solucions algorísmiques basades en la idea de recursivitat o tècniques específiques de disseny d'algorismes."},
        {"code": "SM09", "text": "Utilitzar estructures bàsiques de programació (web, mòbil, núvol) per resoldre problemes simples relacionats amb la gestió de les ciutats, desenvolupant aplicacions informàtiques en entorns web atenent la seva estructura, la interrelació dels components dels servidors i els passos que segueix la gestió de la informació."},
    ],
    "syllabus": [
        {"title": "Continguts", "items": [
            "Introducció a Internet i als servidors Web.",
            "Introducció a JavaScript: sintaxi, variables, tipus, operadors.",
            "Estructures de control: esquema iteratiu i alternatiu.",
            "Tipus de dades estructurades: taules i objectes",
            "Funcions: Declaració, paràmetres, funcions predefinides.",
            "Llenguatge de marcat HTML.",
            "Fulls d'estil CSS, disseny web adaptatiu.",
            "Objectes del navegador (DOM)",
            "Formularis i events",
            "Introducció a les biblioteques o llibreries JavaScript.",
            "Allotjament web",
            "Introducció als gestors de continguts.",
        ]},
    ],
    "teaching_hours": [
        {"title": "Classes pràctiques", "hours": "24", "ects": "0,96"},
        {"title": "Lectura i estudi", "hours": "20", "ects": "0,8"},
        {"title": "Treball basat en problemes", "hours": "45", "ects": "1,8"},
        {"title": "Clase de teoria", "hours": "26", "ects": "1,04"},
        {"title": "Preparació de la presentació del projecte", "hours": "10", "ects": "0,4"},
        {"title": "Tutories", "hours": "10", "ects": "0,4"},
        {"title": "Redacció d'Informes", "hours": "10", "ects": "0,4"},
    ],
    "assessment_items": [
        {"title": "Examen1", "weight": "25"},
        {"title": "Project", "weight": "40"},
        {"title": "Examen2", "weight": "25"},
        {"title": "Problemes", "weight": "10"},
    ],
    "assessment_items_detail": [
        "PART 1: 20% Examen1, 25% Examen2 i 15% Problemes.",
        "PART 2: 40% Projecte",
        "La qualificació de l'assignatura sortirà d'efectuar la suma ponderada de la PART1 i la PART2. La PART1 i la PART2 s'hauran d'aprovar per separat.",
        "La nota de Problemes inclou l'assistència i participació a les classes de problemes i el lliurament dels exercicis proposats.",
        "Per poder aprovar la PART1 serà imprescindible treure almenys un 4 a l'Examen2. En cas que no sigui així la nota de la PART1 seria la resultant de l'Examen2",
    ],
    "assessment_requirements": (
        "Per a cada activitat d'avaluació, s'indicarà un lloc, data i hora de revisió en la que l'estudiant podrà revisar "
        "l'activitat amb el professor. També es podrà demanar la revisió de l'examen mitjançant l'enviament d'un correu "
        "electrònic al responsable de l'assignatura enviat dins de la primera setmana després de la publicació de les notes."
    ),
    "pass_requirements": (
        "Qualsevol estudiant que realitzi almenys una de les components de l'avaluació continuada ja no podrà ser "
        "considerat com No Avaluable. Si no s'arriba a la nota mínima de 5 en alguna de les dues parts (PART1 i/o "
        "PART2) i per aquest motiu no s'aprova l'assignatura, la nota final serà de 4,5 com a màxim. Atorgar una "
        "qualificació de matrícula d'honor és decisió del professorat responsable de l'assignatura: les MH només es "
        "podran concedir si l'estudiant ha obtingut una qualificació final igual o superior a 9.00, fins a un 5% del "
        "total d'estudiants matriculats. Qualsevol estudiant que repeteixi l'assignatura seguirà les mateixes normes "
        "d'avaluació (no es guarda cap nota d'un curs per al següent)."
    ),
    "recovery": (
        "Es farà una prova de reavaluació que inclourà tots els temes tractats a l'assignatura. Aquesta prova permetrà "
        "recuperar els dos exàmens de la PART1. De la PART2 no hi ha recuperació."
    ),
    "ai_policy": (
        "Model 2 - Ús restringit d'IA: Per a aquesta assignatura, es permet l'ús de tecnologies d'Intel·ligència "
        "Artificial (IA) exclusivament en tasques de suport, com la cerca bibliogràfica o d'informació, la correcció "
        "de textos o les traduccions, les activitats i pràctiques però NO a les avaluacions. L'estudiant haurà "
        "d'identificar clarament quines parts han estat generades amb aquesta tecnologia, especificar les eines "
        "emprades i incloure una reflexió crítica sobre com aquestes han influït en el procés i el resultat final de "
        "l'activitat. La no transparència de l'ús de la IA en aquesta activitat avaluable es considerarà falta "
        "d'honestedat acadèmica i pot comportar una penalització parcial o total en la nota de l'activitat, o "
        "sancions majors en casos de gravetat."
    ),
    "software": "Visual Studio Code o Cursor · Github · Navegador",
    "groups_note": "La informació proporcionada és provisional fins al 30 de novembre.",
    "groups": [
        {"kind": "PLAB", "group": "1", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "PLAB", "group": "2", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "TE", "group": "61", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
    ],
    "source": "Programació d'Aplicacions a Internet.pdf (guia docent 2026/2027, extret 2026-09-22)",
}

# ============================================================================
# GP — Gestió de Projectes.pdf (9 页, 原文逐字)
# ============================================================================
GUIA_GP: dict = {
    "course_code": "106938",
    "credits": "6",
    "degree": "Gestió de Ciutats Intel·ligents i Sostenibles",
    "degree_type": "OB",
    "year": "2",
    "academic_year": "2026/2027",
    "contact_email": "dario.cottafava@uab.cat",
    "contact_name": "Dario Cottafava",
    "teaching_team": [],
    "prerequisites": (
        "No hi ha prerequisits específics. Tanmateix, s'espera que l'estudiantat tingui coneixements previs sobre "
        "sostenibilitat, economia circular, sistemes urbans, tecnologies digitals o treball per projectes seran "
        "útils, però no són obligatoris."
    ),
    "objectives": [
        "Objectiu General: L'objectiu principal de l'assignatura és proporcionar a l'estudiantat els conceptes teòrics, les eines pràctiques i les competències de gestió necessàries per dissenyar, planificar, gestionar i avaluar projectes en l'àmbit de les ciutats intel·ligents i sostenibles.",
        "Objectius Teòrics: L'assignatura entén la gestió de projectes com una competència clau per a la transformació urbana. Els projectes de ciutats intel·ligents i sostenibles no són únicament intervencions tècniques; són processos socio-tècnics complexos que impliquen infraestructures, tecnologies, institucions, ciutadania, impactes ambientals, restriccions financeres, stakeholders i reptes de governança a llarg termini. Per aquest motiu, l'assignatura connecta els fonaments de la gestió de projectes amb els estudis de futur, els límits planetaris, l'economia del dònut, el metabolisme urbà, l'economia circular, la bioeconomia, l'economia col·laborativa, l'avaluació d'impacte, la teoria dels stakeholders, la gestió del risc i el lideratge. L'assignatura introdueix també la justícia ambiental i social com a dimensions centrals dels projectes urbans, preguntant qui es beneficia d'un projecte, qui n'assumeix els costos, quins territoris es veuen afectats i com es distribueix el valor entre comunitats i ecosistemes.",
        "Objectius Pràctics: L'estudiantat aplicarà eines i mètodes de gestió de projectes per definir objectius, abast, lliurables, activitats, costos, riscos, stakeholders, impactes i estratègies d'implementació. L'assignatura desenvoluparà la seva capacitat per utilitzar eines com el mapatge de stakeholders, la Teoria del Canvi, la Work Breakdown Structure, el diagrama de Gantt, el PERT, la matriu de riscos, els indicadors d'impacte, el dashboarding, les eines digitals col·laboratives i els mètodes de prototipatge.",
        "Objectiu de Projecte: Una pregunta central de l'assignatura és: quins tipus de projectes, infraestructures i models de governança són necessaris per donar suport a futurs urbans justos, intel·ligents, circulars i regeneratius dins dels límits planetaris?",
    ],
    "learning_outcomes": [
        {"code": "CM13", "text": "Relacionar els coneixements i les habilitats adquirits amb els aportats per altres tècnics en equips interdisciplinaris."},
        {"code": "SM18", "text": "Elaborar de manera bàsica instruments de planificació i planejament en el context de la gestió urbana."},
        {"code": "SM19", "text": "Desenvolupar projectes empresarials relacionats amb la gestió, l'equitat i la sostenibilitat de les ciutats aplicant elements d'innovació tecnològica."},
    ],
    "syllabus": [
        {"title": "TEORIA", "items": [
            "1. Estudis de futur, transicions urbanes i paradigmes de sostenibilitat: Estudis de futur, pensament per escenaris i visions de futurs urbans sostenibles · Límits planetaris, economia del dònut, metabolisme urbà i justícia ambiental · Revolucions industrials, ciutats intel·ligents i transformació de l'entorn urbà · Creixement, postcreixement, decreixement, economia circular, bioeconomia i economia col·laborativa",
            "2. Gestió de projectes: fonaments, eines i aplicacions urbanes: Fonaments de la gestió de projectes: definició, estructura, fases i tipus de projectes · Planificació, organització i execució de projectes: abast, lliurables, fites i recursos · Eines de gestió de projectes: project charter, WBS, Gantt, PERT, matriu RACI i dashboards · Projectes, infraestructures i sistemes urbans: megaprojectes, megaprojectes naturals, solucions basades en la natura i infraestructures postcreixement",
            "3. Avaluació d'impacte, valor social i presa de decisions: Teoria del Canvi: inputs, activitats, outputs, outcomes i impactes · Tipus d'impacte: impactes directes, indirectes i induïts; impactes socials, ambientals i econòmics · Mètodes d'avaluació d'impacte: taules input-output, Anàlisi de Cicle de Vida, anàlisi cost-benefici i avaluació geoespacial · Presa de decisions en contextos complexos: anàlisi multicriteri, valor integrat i indicadors de sostenibilitat",
            "4. Gestió del risc, finances i governança de stakeholders: Identificació, classificació, priorització i mitigació de riscos · Raonament financer: pressupostos, comptes de resultats, tipus d'interès i descompte del futur · Teoria de stakeholders, mapa d'actors, saliència i conflicte ambiental · Governança dels comuns, justícia social, valor integrat i creació de valor públic",
            "5. Project manager, lideratge i metodologies de gestió: El project manager: rols, responsabilitats, funcions directives i competències professionals · Estils de lideratge, comunicació, treball en equip i gestió de conflictes · Metodologies de gestió: waterfall, lean, agile i enfocaments col·laboratius · Comunicació del projecte, reporting i presentació final",
            "6. Regió, bioregió i disseny regeneratiu de projectes: Regió, bioregió i gestió de projectes basada en el lloc · Desenvolupament i disseny regeneratiu: de la sostenibilitat a la regeneració · Agència humana i no humana, hiperobjectes i responsabilitat a llarg termini · Megaprojectes naturals, infraestructures ecològiques i solucions basades en la natura",
        ]},
        {"title": "PRÀCTICA: PROJECTE GRUPAL", "items": [
            "Durant l'assignatura, l'estudiantat treballarà en grups per desenvolupar una proposta de projecte relacionada amb les ciutats intel·ligents i sostenibles. El projecte final es basarà en la identificació d'un repte real de sostenibilitat i en el disseny d'una resposta factible, orientada a l'impacte i ben estructurada. A més de la proposta escrita i la presentació oral, cada grup desenvoluparà un petit prototip, maqueta o demostrador connectat amb la seva idea de projecte.",
            "Opció 1: Campus UAB com a laboratori viu — Els grups podran identificar un repte dins del campus de la Universitat Autònoma de Barcelona i proposar un projecte que contribueixi a fer-lo més intel·ligent, sostenible, circular, inclusiu i resilient.",
            "Opció 2: Barcelona i l'àrea metropolitana — Els grups podran identificar un repte a Barcelona o a l'àrea metropolitana i proposar un projecte que abordi un problema específic de sostenibilitat urbana.",
            "En ambdues opcions, l'estudiantat haurà d'identificar un repte relacionat amb un o més sectors específics. Alguns sectors possibles són residus orgànics, sistemes alimentaris, residus tèxtils, moda circular, illa de calor urbana, infraestructura verda, solucions basades en la natura, mobilitat sostenible, comunitats energètiques, rehabilitació d'edificis, gestió de l'aigua, adaptació climàtica, biodiversitat urbana, regeneració de l'espai públic, plataformes digitals, biomaterials, bioeconomia circular i participació ciutadana.",
        ]},
        {"title": "PRÀCTICA: EXERCICIS DE CLASSE", "items": [
            "1. Three Horizons i visió de futur",
            "2. Metabolisme urbà i mapatge de reptes sectorials",
            "3. Mapa d'actors",
            "4. Mapatge de valor integrat",
            "5. Teoria del Canvi",
            "6. Work Breakdown Structure i diagrama de Gantt",
            "7. PERT i dependències del projecte",
            "8. Matriu de gestió de riscos",
            "9. Estimació de costos i impactes",
            "10. Anàlisi multicriteri",
        ]},
        {"title": "PRÀCTICA: TUTORIALS IT I PROJECTES DIGITALS", "items": [
            "1. Dashboarding amb eines d'IA",
            "2. GitHub i gestió col·laborativa de projectes",
            "3. Aplicacions web no-code, per exemple AppSheet",
            "4. Maker spaces i laboratoris oberts, incloent-hi biomaterials i materials circulars",
            "5. Open data i prototipatge de projectes urbans",
        ]},
    ],
    "teaching_hours": [
        {"title": "Classes pràctiques/Classes teòriques", "hours": "45", "ects": "1,8"},
        {"title": "Preparació i estudi dels continguts teòrics i pràctics", "hours": "90", "ects": "3,6"},
        {"title": "Tutories individuals", "hours": "7,5", "ects": "0,3"},
    ],
    "assessment_items": [
        {"title": "Avaluació teoria (exàmens parcials)", "weight": "50"},
        {"title": "Treball de grup final (Design Thinking)", "weight": "30"},
        {"title": "Treball pràctic en aula i informes", "weight": "20"},
    ],
    "assessment_items_detail": [
        "Avaluació teoria (exàmens parcials): 50% (25%+25%)",
        "Treball de grup final (Design Thinking): 30%",
        "Treball pràctic en aula i informes: 20% (10% assistència + 10% treball pràctic)",
    ],
    "assessment_requirements": (
        "Els continguts d'aquesta assignatura s'avaluaran de forma continuada mitjançant exàmens parcials i avaluacions "
        "d'informes de la part pràctica. Per superar l'assignatura caldrà obtenir un 5 com a nota global ponderada i un "
        "3 sobre 10 de cada activitat d'avaluació per poder fer mitjana. La no participació en alguna de les activitats "
        "específiques es valorarà amb un zero. MH=10. Es considerarà un alumne com a «no avaluable» en el cas que no "
        "participi en cap de les activitats d'avaluació. Al final del curs el professor publicarà les qualificacions "
        "finals i el dia, hora i lloc de revisió de l'examen. En cas d'una nota inferior a 3.5, l'estudiant haurà de "
        "tornar a fer l'assignatura en el següent curs."
    ),
    "pass_requirements": (
        "Aquesta assignatura/mòdul no preveu el sistema d'avaluació única. Hi haurà una re-avaluació per aquells "
        "estudiants que no hagin superat l'assignatura i la seva nota final sigui igual o superior a 3.5. Els "
        "professors de l'assignatura decidiran la modalitat d'aquesta re-avaluació. En cas de superar la re-avaluació, "
        "la nota final serà d'un 5. No hi ha un tractament diferenciat pels estudiants repetidors. La programació de "
        "les proves d'avaluació no es podrà modificar, tret que hi hagi un motiu excepcional i degudament justificat "
        "(Apartat 1 de l'Article 115. Calendari de les activitats d'avaluació, Normativa Acadèmica UAB)."
    ),
    "recovery": (
        "Per participar al procés de recuperació l'alumnat ha d'haver estat prèviament avaluat en un conjunt "
        "d'activitats que representi un mínim de dues terceres parts de la qualificació total de l'assignatura o mòdul "
        "(Apartat 3 de l'Article 112 ter. La recuperació, Normativa Acadèmica UAB). Els estudiants i les estudiants "
        "han d'haver obtingut una qualificació mitjana de l'assignatura entre 3,5 i 4,9. La data d'aquesta prova "
        "estarà programada en el calendari d'exàmens. L'estudiant que es presenti i la superi aprovarà l'assignatura "
        "amb una nota de 5. En cas contrari mantindrà la mateixa nota."
    ),
    "ai_policy": (
        "Ús permès: En aquesta assignatura, es permet l'ús de tecnologies d'Intel·ligència Artificial (IA) com a part "
        "integral del desenvolupament del treball, sempre que el resultat final reflecteixi una contribució "
        "significativa de l'estudiant en l'anàlisi i la reflexió personal. L'estudiant ha d'identificar clarament "
        "quines parts s'han generat amb aquesta tecnologia, especificar les eines utilitzades i incloure una reflexió "
        "crítica sobre com aquestes han influït en el procés i el resultat final de l'activitat. La manca de "
        "transparència en l'ús de la IA es considerarà una manca d'honestedat acadèmica i podrà comportar una "
        "penalització en la nota de l'activitat, o sancions més importants en casos greus."
    ),
    "software": "",
    "groups_note": "La informació proporcionada és provisional fins al 30 de novembre.",
    "groups": [
        {"kind": "TE", "group": "61", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "PAUL", "group": "611", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
    ],
    "source": "Gestió de Projectes.pdf (guia docent 2026/2027, extret 2026-09-22)",
}

# ============================================================================
# GA — Gestió Ambiental de l'Energia i dels Recursos.pdf (7 页, 原文逐字)
# ============================================================================
GUIA_GA: dict = {
    "course_code": "106935",
    "credits": "6",
    "degree": "Gestió de Ciutats Intel·ligents i Sostenibles",
    "degree_type": "FB",
    "year": "2",
    "academic_year": "2026/2027",
    "contact_email": "hyerim.yoon@uab.cat",
    "contact_name": "Hyerim Yoon",
    "teaching_team": ["Genis Riba Sanmarti"],
    "prerequisites": (
        "No és obligatori haver cursat cap assignatura prèviament. En qualsevol cas, per cursar aquesta assignatura "
        "és necessari: Capacitat de comunicació escrita i oral en català i castellà; Nivell mitjà de català, "
        "castellà i anglès, que permeti la comprensió escrita i auditiva en les tres llengües; Nivell mitjà "
        "d'ofimàtica, especialment dels programes de full de càlcul, text i presentacions, ja sigui de MS Office o "
        "de programari lliure."
    ),
    "objectives": [
        "L'assignatura té un objectiu doble. D'una banda proporcionar els coneixements bàsics sobre el context "
        "econòmic, social i territorial en què operen els sistemes energètic i de gestió de recursos a les societats "
        "avançades. L'assignatura, d'altra banda, també té per objectiu que l'alumnat conegui diferents instruments i "
        "mecanismes per a la gestió i la planificació dels recursos.",
        "Pel que fa al primer dels objectius, l'assignatura parteix d'un plantejament introductori dels elements "
        "socioeconòmics i territorials que afecten sistemes amb un elevat nivell de complexitat tècnica com són "
        "l'energia i els recursos. Així, es considera que la configuració i evolució d'aquests sistemes no respon "
        "únicament a un component tècnic o tecnològic sinó que venen clarament condicionades per qüestions tan "
        "diverses com el marc legal i administratiu, les imposicions i els requeriments de l'entorn urbà i territorial "
        "sobre el que operen, l'estructura empresarial en què s'estructura cada sector, el marc geopolític i el "
        "funcionament de l'economia a escala mundial, les pautes de consum i les demandes de la població o el nivell "
        "de sensibilització de la societat envers els impactes d'aquest consum. En aquest sentit, la comprensió de la "
        "lògica de funcionament d'aquests elements de caire socioeconòmic i territorial esdevé fonamental per poder "
        "interpretar les possibilitats de desenvolupament d'un determinat model energètic o de recursos amb èxit. "
        "Aquest primer objectiu s'aborda a la primera part de l'assignatura, la qual es basa principalment en l'àrea "
        "de l'energia per anar abordant de manera detallada cadascuna d'aquestes qüestions. Així, i després d'una "
        "contextualització geogràfica i històrica de l'energia, es detallen els components d'un sistema energètic per "
        "passar posteriorment a descriure el funcionament dels mercats energètics a partir de la descripció dels tres "
        "grans grups d'agents que els integren: els subministradors, els consumidors i l'Administració. Finalment, es "
        "descriuen alguns dels impactes de l'actual model energètic en la nostra societat i s'aporten propostes de "
        "solució a partir del planejament.",
        "Pel que fa al segon objectiu, adquirir una visió global de la gestió ambiental, proporcionarà als estudiants "
        "conceptes fonamentals de la sostenibilitat, així com els mecanismes i polítiques públiques per promoure els "
        "comportaments sostenibles a tots nivells. S'introduiran mètodes de mesura de la sostenibilitat i la seva "
        "aplicació en la gestió de recursos.",
    ],
    "learning_outcomes": [
        {"code": "CM11", "text": "Relacionar els coneixements i les habilitats de gestió i planificació urbanes adquirits amb els aportats per altres tècnics en equips interdisciplinaris."},
        {"code": "KM16", "text": "Analitzar l'entorn urbà des del punt de vista de l'economia circular i la sostenibilitat."},
        {"code": "SM16", "text": "Utilitzar tècniques quantitatives i qualitatives per a l'estudi, la modelització i la planificació dels sistemes energètics, la mobilitat i l'ordenació territorial."},
    ],
    "syllabus": [
        {"title": "Bloc 1: Gestió Ambiental", "items": [
            "Límits al creixement",
            "Sostenibilitat",
            "Cicle hidrosocial",
            "Eines (obligatòries i voluntaries) per a millorar la sostenibilitat",
        ]},
        {"title": "Bloc 2: Energia", "items": [
            "Context geogràfic de l'energia",
            "Context històric de l'energia",
            "Sistemes energètics: definició, components i requeriments",
            "El paper de l'Administració i els planejaments: la UE, l'Estat, la Generalitat i els governs locals",
            "Subministrament energètic: productes derivats del petroli, gas natural i electricitat",
            "El funcionament del mercat de gas, elèctric i combustibles del petroli",
            "Consum energètic: característiques i determinants",
            "Conflictes territorials y socials",
            "Transició energètica",
        ]},
    ],
    "teaching_hours": [
        {"title": "Realització de pràctiques", "hours": "30", "ects": "1,2"},
        {"title": "Lectures orientades", "hours": "10", "ects": "0,4"},
        {"title": "Classes Magistrals", "hours": "30", "ects": "1,2"},
        {"title": "Cerca d'informació", "hours": "6", "ects": "0,24"},
        {"title": "Exercicis dirigits a l'aula (pràctiques)", "hours": "30", "ects": "1,2"},
        {"title": "Lectura i estudi personal", "hours": "10", "ects": "0,4"},
    ],
    "assessment_items": [
        {"title": "Pràctiques energia", "weight": "40"},
        {"title": "Examen Bloc Energia", "weight": "30"},
        {"title": "Pràctiques gestió ambiental", "weight": "15"},
        {"title": "Examen Bloc Gestió Ambiental", "weight": "15"},
    ],
    "assessment_items_detail": [
        "Nota Final = Nota Mòdul ambiental (30%) + Nota Mòdul energia (70%)",
        "La comunicació oficial relacionada amb l'assignatura entre els professors i els estudiants es farà a través de Moodle.",
        "L'avaluació de l'assignatura es farà de forma progressiva i continuada durant tot el semestre.",
    ],
    "assessment_requirements": (
        "Serà condició necessària per poder efectuar la suma ponderada que les pràctiques estiguin aprovades (el que "
        "implica que s'han de fer totes les pràctiques) i que la qualificació obtinguda a cadascun del exàmens sigui "
        "igual o superior a 5. És important recalcar que les pràctiques han de fer-se i entregar-se a les dates "
        "indicades a l'efecte pel professor de l'assignatura. Els estudiants que siguin qualificats de Suspesos per no "
        "haver complert la condició esmentada anteriorment rebran la nota mínima de 3. La data de la revisió es "
        "comunicarà als estudiants a través de Moodle. Idealment, la data es fixarà en un termini de 2 setmanes a "
        "partir de la data de l'examen."
    ),
    "pass_requirements": (
        "La nota Final es calcularà a partir dels dos examens parcials, i la nota de pràctiques: Nota Final = Nota "
        "Mòdul ambiental (30%) + Nota Mòdul energia (70%). En la qualificació dels examens i informes es tindran en "
        "compte aspectes com: presentació de l'examen, redacció, cometre errors bàsics, modificant, si fos necessari, "
        "la nota final obtinguda a partir de la mitjana ponderada de cada una de les notes. La qualificació màxima de "
        "la re-avaluació és un 7. Matrícula d'Honor: les MH només es podran concedir a estudiants que hagin obtingut "
        "una qualificació final igual o superior a 9.00, fins a un 5% del total d'estudiants matriculats. No "
        "avaluable: es considera «no avaluable» un estudiant que no s'hagi presentat a cap examen."
    ),
    "recovery": (
        "Per a aquells estudiants que al final del procés d'avaluació no hagin obtingut una qualificació igual o "
        "superior a 5 a la nota d'exàmens, però tinguin més d'un 5 a les pràctiques, hi haurà una re-avaluació. "
        "Consistirà en la realització, en la data prevista per la Facultat i programada en la darrera setmana del "
        "semestre, d'un examen representatiu de les situacions treballades durant el curs. Els alumnes només "
        "s'hauran de presentar a la part de teoria que no hagin aprovat als examens parcials. Les pràctiques es podran "
        "recuperar si és necessari per assolir una nota mitjana de treball pràctic superior a 5. Pels alumnes "
        "repetidors, la nota de teoria de les parts aprovades no es guarda d'un curs per l'altre. Però, la nota de les "
        "pràctiques sí que es guardarà d'un curs per l'altre. La qualificació màxima de la re-avaluació és un 7."
    ),
    "ai_policy": (
        "Ús restringit: Per a aquesta assignatura, es permet l'ús de tecnologies d'Intel·ligència Artificial (IA) "
        "exclusivament en tasques de suport, com la cerca bibliogràfica o d'informació, la correcció de textos o les "
        "traduccions. L'estudiant haurà d'identificar clarament quines parts han estat generades amb aquesta "
        "tecnologia, especificar les eines emprades i incloure una reflexió crítica sobre com aquestes han influït en "
        "el procés i el resultat final de l'activitat. La no transparència de l'ús de la IA en aquesta activitat "
        "avaluable es considerarà falta d'honestedat acadèmica i pot comportar una penalització parcial o total en la "
        "nota de l'activitat, o sancions majors en casos de gravetat. Aquesta assignatura no preveu el sistema "
        "d'avaluació única."
    ),
    "software": "MS Excel, SankeyMATIC",
    "groups_note": "La informació proporcionada és provisional fins al 30 de novembre.",
    "groups": [
        {"kind": "PAUL", "group": "1", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "TE", "group": "61", "language": "Català/Castellà", "semester": "primer quadrimestre", "shift": "tarda"},
    ],
    "source": "Gestió Ambiental de l'Energia i dels Recursos.pdf (guia docent 2026/2027, extret 2026-09-22)",
}

# ============================================================================
# MC — Digitalització i Microcontroladors.pdf (6 页, 原文逐字)
# ============================================================================
GUIA_MC: dict = {
    "course_code": "106936",
    "credits": "6",
    "degree": "Gestió de Ciutats Intel·ligents i Sostenibles",
    "degree_type": "OB",
    "year": "2",
    "academic_year": "2026/2027",
    "contact_email": "jordi.castilla@uab.cat",
    "contact_name": "Jordi Castilla Miro",
    "teaching_team": ["Irene Gutiérrez del Burgo"],
    "prerequisites": (
        "Per a la plena comprensió dels continguts de l'assignatura convé tenir una habilitat bàsica en la "
        "programació i un bon coneixement de com s'executen els programes en els computadors. Per a això, s'ha "
        "d'haver cursat Informàtica i Programació d'aplicacions a Internet. Com que els programes es relacionen "
        "directament amb dispositius externs, també és necessari haver cursat Fonaments d'electrònica i "
        "Instrumentació i sensors."
    ),
    "objectives": [
        "Tenir una visió global de la digitalització de dades, entenent la seva utilitat i necessitat.",
        "Conèixer els principals tipus de sensors i els senyals que proporcionen.",
        "Conèixer les arquitectures bàsiques de microcontroladors.",
        "Conèixer les alternatives tecnològiques per al prototipatge de sistemes basats en microcontroladors.",
        "Desenvolupar un sistema basat en un microcontrolador de forma bàsica.",
        "Aprendre els conceptes bàsics en el tractament del temps real i de l'ús de sistemes operatius en temps real (RTOS).",
        "Ser capaç d'avaluar les prestacions d'un sistema basat en microcontroladors.",
    ],
    "learning_outcomes": [
        {"code": "CM17", "text": "Distingir els costos econòmics i mediambientals de l'ús de les tecnologies de la informació i la comunicació."},
        {"code": "KM22", "text": "Descriure les tecnologies de captació i transmissió de dades, així com d'actuadors i sistemes robòtics i la problemàtica associada a la seva integració en el teixit urbà."},
        {"code": "SM21", "text": "Utilitzar els sistemes d'adquisició de dades (com, per exemple, sensors i etiquetes RFID) i el seu processament com a eina de control (per exemple, d'instrumentació i robots) i presa de decisions."},
    ],
    "syllabus": [
        {"title": "Continguts", "items": [
            "Introducció al disseny de sistemes basats en microcontroladors",
            "Arquitectures bàsiques de microcontroladors",
            "Digitalizació",
            "Entrada/sortida analògica i digital",
            "Interfície entre microcontrolador i sensors",
            "Protocols de comunicació per a sensors",
            "Plataformes de desenvolupament basades en microcontroladors",
            "Programació de microcontroladors",
            "Processament de senyals",
            "Controladors basats en estats",
        ]},
    ],
    "teaching_hours": [
        {"title": "Problemes i treball a classe", "hours": "12", "ects": "0,48"},
        {"title": "Teoria", "hours": "20", "ects": "0,8"},
        {"title": "Classes pràctiques dirigides", "hours": "12", "ects": "0,48"},
        {"title": "Elaboració d'informes", "hours": "8", "ects": "0,32"},
        {"title": "Lectura i estudi de material", "hours": "14", "ects": "0,56"},
        {"title": "Avaluació", "hours": "5", "ects": "0,2"},
    ],
    "assessment_items": [
        {"title": "Avaluació continuada (2 blocs)", "weight": "75"},
        {"title": "Pràctiques", "weight": "25"},
    ],
    "assessment_items_detail": [
        "Avaluació continuada: Temes 1, 2, 3 — 50% (nota mínima per fer mitjana 4.0); Temes 4, 5 — 50% (nota mínima per fer mitjana 4.0)",
        "Nota final: Avaluació continuada 75% + Pràctiques 25%",
        "Es faran un total 4 pràctiques i la nota final serà la mitjana ponderada. Les pràctiques es faran en grups de dues persones.",
    ],
    "assessment_requirements": (
        "Aprovat: es considera aprovat tot aquell que tingui la nota d'avaluació continuada igual o superior a 5, "
        "tingui la nota de pràctiques igual o superior a 5, i no hi hagi cap prova de l'avaluació continuada per sota "
        "de la nota mínima (4.0) per fer mitjana. Lliuraments: els lliuraments fora de termini, sempre que hi hagi "
        "previ avís i estigui justificat, seran acceptats i penalitzats amb una nota més baixa. En cap cas "
        "s'admetran lliuraments fora de termini sense avís previ o justificació de força major. Els treballs no "
        "lliurats rebran una nota de 0 i no tindran opció a una segona avaluació ni a recuperació. Les pràctiques "
        "compten un 25% de la qualificació final. Revisions: fins a dues setmanes després de la publicació de les "
        "notes i abans del termini de revisió de l'examen de recuperació."
    ),
    "pass_requirements": (
        "Aquesta assignatura no preveu el sistema d'avaluació única. En cas de no superar l'assignatura degut a que "
        "alguna de les activitats d'avaluació no arriba a la nota mínima requerida, la nota numèrica de l'expedient "
        "serà el valor MENOR entre 4.5 i la mitjana ponderada de les notes. La qualificació de «no avaluable» només "
        "s'atorgarà a les persones que no facin CAP activitat avaluable (avaluació continuada, examen final i "
        "pràctiques). La participació en UNA activitat avaluable implica que la resta d'activitats que no es facin "
        "computaran com a 0 en el càlcul de la nota final. Les matrícules d'honor es concediran als qui obtinguin una "
        "nota superior o igual a 9,0 a cada part, fins al 5% dels matriculats segons ordre descendent de nota final. "
        "Per a alumnes que repeteixin l'assignatura, es mantindrà la nota de les pràctiques DEL CURS IMMEDIATAMENT "
        "ANTERIOR si així ho demanen al principi del curs, en cas contrari hauran de fer-les OBLIGATÒRIAMENT."
    ),
    "recovery": (
        "Hi haurà un examen de recuperació de cadascun dels dos blocs de teoria destinat a recuperar la part no "
        "superada de l'avaluació continuada. Aquest examen de recuperació també podrà utilitzar-se per millorar la "
        "nota de l'avaluació continuada de cada bloc, si així ho desitgen. En cap cas la nota resultant d'aquestes "
        "proves baixarà la de l'avaluació continuada. L'examen de recuperació es farà de forma individual. Examen de "
        "recuperació: Temes 1, 2, 3 — 50% (nota mínima 4.0); Temes 4, 5 — 50% (nota mínima 4.0). Nota final de la "
        "recuperació: Examen de recuperació 75% + Pràctiques 25%."
    ),
    "ai_policy": (
        "En aquesta assignatura, es permet l'ús de tecnologies d'Intel·ligència Artificial (IA) com a part integrant "
        "del desenvolupament del treball, sempre que el resultat final reflecteixi una contribució significativa de "
        "l'estudiant en l'anàlisi i la reflexió personal. L'estudiant haurà d'identificar clarament quines parts han "
        "estat generades amb aquesta tecnologia, especificar les eines emprades i incloure una reflexió crítica sobre "
        "com aquestes han influït en el procés i el resultat final de l'activitat. La no transparència de l'ús de la "
        "IA es considerarà falta d'honestedat acadèmica i pot comportar una penalització en la nota de l'activitat, o "
        "sancions majors en casos de gravetat."
    ),
    "software": "Per les parts de problemes i de pràctiques de l'assignatura es farà servir l'entorn de treball d'Arduino.",
    "groups_note": "La informació proporcionada és provisional fins al 30 de novembre.",
    "groups": [
        {"kind": "TE", "group": "61", "language": "Català", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "PAUL", "group": "611", "language": "Català", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "PLAB", "group": "611", "language": "Català", "semester": "primer quadrimestre", "shift": "tarda"},
        {"kind": "PLAB", "group": "612", "language": "Català", "semester": "primer quadrimestre", "shift": "tarda"},
    ],
    "source": "Digitalització i Microcontroladors.pdf (guia docent 2026/2027, extret 2026-09-22)",
}


def main() -> int:
    guias = {
        "course-bd4f98777c97a0d2": GUIA_PA,
        "course-32dde014868219be": GUIA_GP,
        "course-680f27ea0e304d6d": GUIA_GA,
        "course-6ea554f295431036": GUIA_MC,
    }
    failed = []
    for course_id, guia in guias.items():
        # 1. 读当前课程 (拿已有 metadata, 合并而不是覆盖)
        with urllib.request.urlopen(f"{BASE}/api/courses/{course_id}", timeout=10) as resp:
            current = json.load(resp)["data"]
        merged = dict(current.get("metadata") or {})
        merged.update({f"guia_{key}": value for key, value in guia.items()})

        # 2. PATCH 写回 (走运行中的服务 -> 内存态与数据库两边都改)
        req = urllib.request.Request(
            f"{BASE}/api/courses/{course_id}",
            data=json.dumps({"metadata": merged}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            updated = json.load(resp)["data"]

        # 3. 读回验证
        got = {k: v for k, v in updated.get("metadata", {}).items() if k.startswith("guia_")}
        expected_keys = {f"guia_{key}" for key in guia if guia[key] != "" or key == "software"}
        # 空字符串键也写入 (保持键齐全), 验证时排除纯空值
        written_expected = {f"guia_{key}" for key in guia}
        missing = written_expected - set(got)
        n = len([1 for k in expected_keys if got.get(k)])
        print(f"{COURSES[course_id]} {course_id}: {len(got)} guia_* keys written, {n} non-empty")
        if missing:
            print("  MISSING:", sorted(missing))
            failed.append(course_id)
    if failed:
        print("FAILED:", failed)
        return 1
    print("OK: 4 courses imported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
