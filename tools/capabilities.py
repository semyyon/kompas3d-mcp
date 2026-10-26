# -*- coding: utf-8 -*-
"""Опись возможностей КОМПАС-3D v23 для MCP kompas3d → docs/kompas-capabilities.{json,md}.

    python tools/capabilities.py            # пересобрать опись
    python tools/capabilities.py --check    # только проверить имена интерфейсов

Три слоя:
  1. Типы объектов модели (ksObj3dTypeEnum) и графического документа (DrawingObjectTypeEnum)
     с соответствием API5 ↔ API7 — из справки SDK (stubs) и библиотек типов v23 (api_index).
  2. Функции, которых нет среди типов объектов (документы, свойства, переменные, исполнения,
     сопряжения, спецификация, библиотеки, экспорт…) — ручной список cap(...) ниже, по разделам
     справки КОМПАС; имена интерфейсов сверяются с индексом (опечатка → ошибка генератора).
  3. Покрытие MCP (COVER, cap(..., mcp)) и живые проверки
     (docs/kompas-capabilities.checks.json: {ключ: {"result": "PASS"|"FAIL", "date", "note"}}).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import api_index  # noqa: E402

ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "docs", "kompas-capabilities.json")
OUT_MD = os.path.join(ROOT, "docs", "kompas-capabilities.md")
CHECKS = os.path.join(ROOT, "docs", "kompas-capabilities.checks.json")

GW = "kompas_api"   # универсальный шлюз (план): всё, у чего есть интерфейс, доступно через него

# ── 1. Разделы для типов объектов модели (по имени константы, первое совпадение) ──
AREAS_3D = [
    ("3D: служебные объекты API", r"^o3d_(unknown|variable|placement|entityCollection|document|part|entity$|"
     r"mateConstraint|partCollection|constrElement|operationElement|curveElement|rasterFormat|additionFormat|"
     r"bodyCollection|body$|faceCollection|tessellation|facet|featureCollection|feature$|edgeCollection|"
     r"orientedEdge|loop|curve3D$|surface$|massInertiaParam|\w+Param$|nurbs\w*Coll|nurbsKnot|measurer|"
     r"selectionMng|chooseMng|deletedCopyCollection|viewProjection|attribute|componentPositioner|"
     r"modelLibrary|ObjectsFilter3D|coordinate3dCollection|intersectionResult|PolygonalLineVertexParam|"
     r"variableCollection|sTrackingPointsMeasurer|surfaceElement|designationElemen|copyleftObject|"
     r"Embodiment|userMateConstraint|intervalVariable|userFunc|firstEntityElement2|lastEntityElement|"
     r"face$|edge$|vertex$|ArrayExemplar|AuxGeomArrayExemplar|BodyArrayExemplar|UndefPartObject|"
     r"UserFolder|lineseg3dParam|designationElement|aggregate)"),
    ("3D: листовое тело", r"sheetMetal|SubFoldLine"),
    ("3D: массивы и копирование", r"Copy|Pattern|PartArray|mirrorOperation|mirrorAllOperation|MirrorPart|"
     r"AuxMirror|PointsArr"),
    ("3D: обозначения и размеры в модели", r"Dimension|leader3D|Leader3D|rough3D|Rough3D|base3D|Base3D|"
     r"tolerance3D|TechnicalDemand3D|Text3D|ModelText|ModelTable|UserDesignation|thread|Thread"),
    ("3D: вспомогательная геометрия", r"plane|Plane|axis|Axis|pointCS|controlPoint|conjunctivePoint|point3D|"
     r"localCoordinateSystem|LocalCS"),
    ("3D: поверхности", r"Surface|surface|Patch|FaceRemover|Sewer|Nurbs|RestoredSurface|Sphere|Cylinder|"
     r"Block|Torus|Cone"),
    ("3D: кривые и точки", r"polyline|spline|Spline|Spiral|Curve|curve|Contour3D|Arc3D|LineSegment3D|"
     r"PolyLine3D|Equidistant3D|Isoparametric|WireFrame|ConicCurve3D|Vector"),
    ("3D: анализ и измерения", r"Measure|Curvature|Collision|Continuity|SectionAnalysis|DynamicCrossSection|"
     r"Diagnostic|Deviation"),
    ("3D: сервис модели", r"Zone|MacroObject|UnHistoried|Unhistoried|Billet|SketchFace|UserObject|"
     r"UserComponent|CollectionGeometry|CopyGeometry"),
    ("3D: тела и операции", r".*"),
]

AREAS_2D = [
    ("2D: размеры", r"Dimension"),
    ("2D: обозначения", r"Rough|Base$|WPointer|Cut|Leader|Tolerance|ChangeLeader|RemoteElement|CentreMarker|"
     r"AxisLine|UnitNumber|Mark|Brace|StraightAxis|CircleAxis|ArcAxis|UnitMarking|ConditionCrossing|"
     r"Centres|ArcAxis|BrokenLine|WaveLine"),
    ("2D: текст и таблицы", r"Text|Table"),
    ("2D: виды, слои, вставки", r"View$|Layer|Fragment|Macro|Raster|OLE|ExternalView"),
    ("2D: геометрия", r".*"),
]

# ── 2. Покрытие MCP для типов объектов ──
COVER = {
    "o3d_sketch": ("kompas_shape", "частично: отрезки, полилинии, окружности, шестигранник, ось"),
    "o3d_baseExtrusion": ("kompas_shape", "op=base"),
    "o3d_bossExtrusion": ("kompas_shape", "op=boss"),
    "o3d_cutExtrusion": ("kompas_shape", "op=cut, through; защита от выреза «в воздух»"),
    "o3d_baseRotated": ("kompas_shape", "op=revolve"),
    "o3d_planeOffset": ("kompas_shape", "plane+offset — смещённая плоскость под эскиз"),
    "o3d_planeXOY": ("kompas_shape", "plane=xoy"), "o3d_planeXOZ": ("kompas_shape", "plane=xoz"),
    "o3d_planeYOZ": ("kompas_shape", "plane=yoz"),
    "o3d_thread": ("kompas_model_summary", "только чтение: резьбы модели"),
    "o3d_DynamicCrossSection": ("kompas_run_job", "dyn_section: показ сечения, модель не меняется"),
    "o3d_part": ("kompas_insert / kompas_move / kompas_get_tree", "вставка, перемещение, обход"),
    "o3d_body": ("kompas_measure", "число тел"),
    "o3d_face": ("kompas_model_summary", "только чтение: грани, отверстия, плоскости"),
    "o3d_massInertiaParam": ("kompas_measure / kompas_part_props", "масса, плотность"),
    "ksDrLDimension": ("kompas_build_drawing / kompas_place_dimensions", ""),
    "ksDrDDimension": ("kompas_build_drawing / kompas_place_dimensions", ""),
    "ksDrRDimension": ("kompas_build_drawing / kompas_place_dimensions", ""),
    "ksDrADimension": ("kompas_build_drawing", "угловые размеры (PCD)"),
    "ksDrRough": ("kompas_build_drawing", ""),
    "ksDrBase": ("kompas_build_drawing", ""),
    "ksDrTolerance": ("kompas_build_drawing", ""),
    "ksDrCut": ("kompas_build_drawing", "разрезы/сечения"),
    "ksDrRemoteElement": ("kompas_build_drawing", "выносные элементы"),
    "ksDrLeader": ("kompas_build_drawing", "выноски (резьба, надписи)"),
    "ksDrAxisLine": ("kompas_build_drawing", "оси симметрии"),
    "ksDrCentreMarker": ("kompas_build_drawing", "обозначения центров"),
    "ksDrLineSeg": ("kompas_build_drawing", "лучи PCD, отрезки"),
    "ksDrCircle": ("kompas_build_drawing", "окружность центров"),
    "ksDrDrawText": ("kompas_build_drawing", ""),
    "ksView": ("kompas_build_drawing", "ассоциативные виды, разрезы, местные виды"),
    "ksDrHatch": ("kompas_build_drawing", "стиль штриховки разреза"),
}

# ── 3. Функции вне перечней типов (ручной слой). api — интерфейсы/члены, проверяются по индексу ──
#   ключ: короткий латинский идентификатор (для checks.json и шлюза)
C = []


def cap(key, area, func, api, mcp="", note="", prio=""):
    C.append({"key": key, "area": area, "func": func, "api": api, "mcp": mcp, "note": note, "prio": prio})


A = "Документы и приложение"
cap("doc.create", A, "Создать документ (деталь, сборка, чертёж, фрагмент, спецификация, текст)",
    ["IDocuments.Add"], "kompas_create_document")
cap("doc.open", A, "Открыть документ (видимый/невидимый, только чтение)", ["IDocuments.Open"], "kompas_open_document")
cap("doc.save", A, "Сохранить / Сохранить как (с бэкапом сборки)", ["IKompasDocument.Save", "IKompasDocument.SaveAs"],
    "kompas_save_document")
cap("doc.close", A, "Закрыть документ", ["IKompasDocument.Close"], "kompas_close_document",
    "адресно: document=")
cap("doc.list", A, "Список открытых документов, активный документ", ["IApplication.Documents", "IApplication.ActiveDocument"],
    "kompas_status")
cap("doc.prev_version", A, "Сохранить в формате предыдущей версии КОМПАС", ["IKompasDocument1.SaveAsEx", "ISaveToPreviusParam7"], GW)
cap("doc.template", A, "Создать документ по шаблону (.cdt/.spt/.m3t)", ["IDocuments.AddWithDefaultSettings"], GW)
cap("doc.rebuild", A, "Перестроить документ", ["IKompasDocument3D.RebuildDocument", "IKompasDocument2D1.RebuildDocument"], GW)
cap("doc.app_info", A, "Версия, видимость, сообщения КОМПАС (подавить диалоги)",
    ["IApplication.ApplicationName", "IApplication.HideMessage", "IApplication.Visible"], "kompas_status / kompas_auto_dialogs")
cap("doc.undo", A, "Отмена/повтор (транзакции)", ["IKompasDocument3D.UndoContainer", "IKompasDocument2D1.UndoContainer"], GW)

A = "3D: тела и операции"
cap("op.list", A, "Перечень операций модели и их параметры (дерево построения)",
    ["IModelContainer", "IFeature7.SubFeatures", "IPart7.Owner"], "kompas_get_tree", "дерево компонентов есть; дерево ОПЕРАЦИЙ детали — нет", "частые")
cap("op.edit", A, "Изменить параметр существующей операции (глубина, угол) и перестроить",
    ["IExtrusion.Depth", "IModelObject.Update"], GW, "", "частые")
cap("op.delete", A, "Удалить / исключить из расчёта операцию", ["ksDocument3D.DeleteObject", "ksEntity.excluded"], GW)
cap("op.hole_lib", A, "Отверстие из библиотеки (с резьбой, зенковкой) — Hole3D", ["IHoles3D.Add", "IHole3D"], GW, "", "частые")
cap("op.thread", A, "Условное изображение резьбы на цилиндре", ["IThreads.Add", "IThread", "ksThreadDefinition"],
    "kompas_feature", "op=thread, грань по точке", "частые")
cap("op.chamfer_fillet", A, "Фаска и скругление по рёбрам", ["IChamfers.Add", "IFillets.Add", "ksChamferDefinition"],
    "kompas_feature", "op=chamfer|fillet, рёбра по точкам (SelectByPoint) — закрывает урок 18", "частые")
cap("op.boolean", A, "Булева операция над телами", ["IBooleans.Add", "IBoolean"], GW)
cap("op.split", A, "Разделить тело", ["ISplitSolids.Add"], GW)
cap("op.direct", A, "Прямое моделирование: сдвиг граней", ["IFaceMovers.Add"], GW)
cap("op.reposition", A, "Изменить положение тела", ["IBodyRepositions.Add"], GW)
cap("op.unhistory", A, "Удалить историю построения модели", ["IKompasDocument3D.DeleteHistory"], GW)

A = "3D: вспомогательная геометрия"
cap("aux.lcs", A, "Локальная система координат", ["ILocalCoordinateSystems.Add", "ILocalCSObject"], GW)
cap("aux.points", A, "Контрольные и присоединительные точки", ["IControlPoints", "IConjunctivePoints"], GW)

A = "Сборка: компоненты и сопряжения"
cap("asm.insert", A, "Вставить компонент из файла (внешней ссылкой) с размещением", ["IParts7.AddFromFile", "ksPart.SetPlacement"],
    "kompas_insert")
cap("asm.move", A, "Переместить/повернуть компонент со сверкой", ["ksPart.SetPlacement", "ksPart.UpdatePlacement"], "kompas_move")
cap("asm.tree", A, "Дерево сборки с координатами", ["IPart7.Parts", "IPart7.Placement"], "kompas_get_tree / kompas_run_job(state_dump)")
cap("asm.delete", A, "Удалить компонент", ["ksDocument3D.DeleteObject"], GW, "", "частые")
cap("asm.replace", A, "Заменить компонент другим файлом (с сохранением положения)", ["IPart7.FileName"], GW, "", "частые")
cap("asm.hide", A, "Скрыть/показать компоненты", ["IModelObject.Hidden"], "kompas_run_job(keep_only)", "только «оставить список»")
cap("asm.fix", A, "Зафиксировать компонент", ["IPart7.Fixed"], GW)
cap("asm.mates", A, "Сопряжения: создать (совпадение, соосность, расстояние, угол…), перечень, удалить",
    ["IMateConstraints3D.Add", "IMateConstraint3D", "ksMateConstraint"], GW,
    "проект ставит компоненты матрицами; сопряжения — для подвижных узлов", "частые")
cap("asm.mirror", A, "Зеркальный/симметричный компонент", ["IPart7.MirroringPlacement"], GW, "создание — только интерфейс пользователя?")
cap("asm.patterns", A, "Массивы компонентов (по сетке, по концентрической, по образцу, по точкам)",
    ["IFeaturePatterns", "ICircularPattern", "ILinearPattern", "IDerivedPattern"], GW,
    "проект вставляет крепёж через kompas_insert по координатам — массив по образцу короче", "частые")
cap("asm.subassembly", A, "Создать подсборку из компонентов / группировать", ["IParts7.AddFromFile"], GW,
    "IUserFolders (папки) в v23 через COM не работают — урок 12")
cap("asm.local_part", A, "Локальная деталь (создать деталь в контексте сборки)", ["IParts7.Add"], GW)
cap("asm.dismantle", A, "Разнесение компонентов сборки", ["IDismantleParameters", "IDismantleStep"], GW)
cap("asm.loadmode", A, "Неполная загрузка сборки (комбинации загрузки)", ["ILoadCombination", "ILoadCombinationsParam"], GW)
cap("asm.save_as_part", A, "Сохранить сборку как деталь / деталь как сборку", ["ISaveAsDetailParam"], GW)
cap("asm.fasteners_audit", A, "Аудит крепежа: группы, количества, оси, дубли", ["IPart7.Parts"], "kompas_audit_fasteners")
cap("asm.collisions", A, "Проверка пересечений компонентов/тел (объём пересечения)",
    ["ksMeasurer", "IBody7.CheckIntersectionWithBody"], "kompas_interference",
    "пары с объёмным пересечением; объём в мм³ API не отдаёт", "частые")

A = "Свойства, материал, МЦХ, цвет"
cap("prop.basic", A, "Имя, обозначение, материал, плотность, цвет детали", ["IPart7.Name", "IPart7.Marking",
    "IPart7.SetMaterial", "IColorParam7"], "kompas_part_props")
cap("prop.mass", A, "Масса, центр масс, моменты инерции", ["IMassInertiaParam7"], "kompas_measure", "масса есть; центр масс и моменты — нет")
cap("prop.gabarit", A, "Габарит модели/компонента", ["ksPart.GetGabarit"], "kompas_measure / kompas_get_bbox")
cap("prop.user", A, "Свойства документа/объекта (Менеджер свойств): прочитать, создать, задать",
    ["IPropertyMng", "IPropertyKeeper", "IProperty"], GW, "нужны для штампа, спецификации, отчётов", "частые")
cap("prop.texture", A, "Оптические свойства, текстура", ["ITexturesParam", "IColorParam7"], GW)
cap("prop.hatch", A, "Параметры штриховки модели для разрезов", ["IPart7.HatchParam"], GW)

A = "Переменные, исполнения, семейства"
cap("var.list", A, "Переменные модели/документа: перечень, значение, выражение, внешние",
    ["IFeature7.Variables", "IVariable7"], "kompas_variables", "и параметры операций (features=True)", "частые")
cap("var.set", A, "Задать значение/выражение переменной и перестроить", ["IVariable7.Value", "IVariable7.Expression",
    "IPart7.AddVariable"], "kompas_variables", "set/add, перестроение, масса и габарит до/после", "частые")
cap("var.table", A, "Таблица переменных / информационные переменные", ["IVariable7.External"], GW)
cap("emb.manage", A, "Исполнения модели: создать, переключить, обозначение исполнения",
    ["IEmbodimentsManager", "IEmbodiment"], GW, "", "частые")
cap("family.manage", A, "Семейства моделей: таблица, применить строку, сгенерировать файлы",
    ["IModelsFamilyManager.ApplyLine", "IModelsFamilyManager.Generate"], GW,
    "есть только в v23 (typelib), в stubs нет")
cap("var.tolerance_recalc", A, "Пересчёт модели с учётом допусков", ["IToleranceRecalcsManager"], GW)

A = "Измерения и анализ модели"
cap("meas.basic", A, "Расстояние/угол между объектами, длина ребра, площадь", ["ksMeasurer",
    "IDistanceAngleMeasurements3D", "IEdgeLengthMeasurements3D", "IAreaMeasurements3D"], GW)
cap("meas.faces", A, "Грани, отверстия, плоскости детали (сводка для чертежа)", ["IFace", "IMathSurface3D"], "kompas_model_summary")
cap("meas.view", A, "Виды модели и скриншот / растровый рендер", ["IViewProjectionManager"], "kompas_view / kompas_export_raster")

A = "Сервис модели"
cap("svc.section", A, "Сечение модели (динамическое, плоскостью)", ["IDynamicCrossSectionsManager"], "kompas_run_job(dyn_section)")
cap("svc.layers", A, "Слои в модели", ["ILayers3D"], GW)
cap("svc.zones", A, "Зоны в модели", ["IZonesManager"], GW)
cap("svc.macro", A, "Трёхмерный макроэлемент", ["IMacroObjects3D"], GW)
cap("svc.tt_model", A, "Технические требования и неуказанная шероховатость в модели", ["ITechnicalDemand3D", "ISpecRough3D"], GW)
cap("svc.tech_asm", A, "Технологическая сборка", [], "", "в API не найдено — только интерфейс пользователя")

A = "Чертёж: листы и виды"
cap("drw.build", A, "Чертёж детали по drawing_spec одним вызовом (виды, разрезы, размеры, ТТ, штамп, проверка)",
    ["IViewsAndLayersManager", "IAssociationView"], "kompas_build_drawing")
cap("drw.check", A, "Проверка чертежа по правилам проекта (15 правил)", [], "kompas_check_drawing")
cap("drw.snapshot", A, "Картинка листа/вида + факты", [], "kompas_snapshot")
cap("drw.assoc_views", A, "Ассоциативные виды из модели (стандартные, проекционные, по стрелке)",
    ["IViews.Add", "IAssociationView"], "kompas_build_drawing", "только через spec")
cap("drw.sheets", A, "Листы: добавить, формат, ориентация", ["ILayoutSheets", "ILayoutSheet", "ISheetFormat"], "kompas_build_drawing",
    "формат/второй лист — через spec")
cap("drw.assembly_drawing", A, "Сборочный чертёж (виды сборки, позиции, связь со спецификацией)",
    ["IAssociationView", "IPositionLeader"], GW, "draftsman: «сборочные чертежи пока не в компетенции»", "частые")
cap("drw.break_view", A, "Разрыв вида", ["IBreakViewParam"], GW)
cap("drw.insert_view", A, "Вставка вида другого чертежа / фрагмента", ["IInsertionsManager", "IInsertionView", "IInsertionFragment"], GW)

A = "Чертёж: текст, таблицы, ТТ, штамп"
cap("drw.stamp", A, "Основная надпись: заполнить ячейки", ["IStamp"], "kompas_build_drawing", "через spec")
cap("drw.tt", A, "Технические требования", ["ITechnicalDemand"], "kompas_build_drawing", "через spec")
cap("drw.spec_rough", A, "Неуказанная шероховатость", ["ISpecRough"], "kompas_build_drawing", "через spec")
cap("drw.tables", A, "Таблицы на чертеже (параметры зубчатого колеса, исполнений)", ["IDrawingTable", "ITable"], GW)
cap("drw.groups", A, "Именованные группы объектов", ["IDrawingGroups"], GW)
cap("drw.autonumber", A, "Автонумерация и автосортировка объектов (позиции)", ["IAutoSortObjectsParameters"], GW)
cap("drw.param", A, "Параметризация: ограничения 2D", ["IParametriticConstraint"], GW)
cap("drw.measure2d", A, "Измерения и МЦХ в графическом документе", ["ksMeasurer"], GW)

A = "Спецификация, состав изделия, отчёты"
cap("spc.objects", A, "Объекты спецификации: создать, привязать к компоненту/позиции, раздел",
    ["ISpecificationDescription", "ISpecificationObject", "ISpecificationBaseObjects"], GW, "", "частые")
cap("spc.document", A, "Документ-спецификация по сборке (связь со сборкой и сборочным чертежом)",
    ["IAttachedDocuments.AddDocument", "ISpecificationDescription"], GW,
    "рецепт: новый .spw → doc.AttachedDocuments.AddDocument(сборка, True, True, \"\") — разделы заполняются сами", "частые")
cap("spc.style", A, "Стиль спецификации, настройка разделов", ["ISpecificationStyle", "ISpecificationTuning"], GW)
cap("spc.bom", A, "Состав изделия (BOM): прочитать как таблицу", ["IPart7.Parts", "IPart7.Standard"], "kompas_bom",
    "первый уровень как в спецификации ЕСКД или flat; разделы, количество, масса", "частые")
cap("rep.report", A, "Отчёты (ассоциативная таблица отчёта)", ["IReport", "IAssociationTable"], GW)

A = "Библиотеки и приложения"
cap("lib.list", A, "Перечень прикладных библиотек, подключение", ["ILibraryManager.ProceduresLibraries", "ILibrary.Attach"], GW,
    "проверено 2026-10-19: 35 приложений, 7 библиотек документов")
cap("lib.run", A, "Запустить команду приложения (Валы, Пружины, Трубопроводы…)", ["ILibrary.Execute", "IProcedure.Execute"], GW,
    "команды приложений интерактивны (диалоги) — автоматизация ограничена")
cap("lib.kle", A, "Библиотеки документов .kle (конструктивные элементы, «Модели крепежных элементов»)",
    ["ILibraryManager.DocumentsLibraries", "IInsert.Edit"], GW)
cap("lib.l3d", A, "Библиотеки моделей .l3d", ["ksModelLibrary", "ILibraryManager.ModelsLibraries"], GW)
cap("lib.std_products", A, "«Стандартные изделия» (ПОЛИНОМ): крепёж, подшипники по ГОСТ/ISO", [], "",
    "в API КОМПАС нет; отдельный COM без pythonnet: Ascon.PartLib.Integrator.GetPartLibObjectSilent(location, docType), "
    "PolynomHelper.PolynomFuncs23.GetFileName/GetVariables/GetProperties(location) — нужен формат location "
    "(взять из изделия, вставленного вручную); команда «Вставить элемент» открывает каталог для пользователя", "частые")
cap("lib.materials", A, "«Материалы и сортаменты» (ПОЛИНОМ)", ["IPart7.SetMaterial"], "kompas_part_props", "материал строкой; справочник — нет")

A = "Импорт, экспорт, печать"
cap("io.export3d", A, "Экспорт модели: STEP, IGES, Parasolid, STL, JT, IFC", ["IKompasDocument.SaveAs"], "kompas_save_document",
    "по расширению; параметры конвертера — нет")
cap("io.export2d", A, "Экспорт чертежа: PDF, DXF/DWG, растр", ["IKompasDocument.SaveAs"], "kompas_save_document / kompas_export_raster")
cap("io.import", A, "Импорт STEP/IGES/… как модели", ["IDocuments.Open"], "kompas_open_document")
cap("io.print", A, "Печать, задания на печать", [], "", "не в этот раз")

A = "Настройки, стили, атрибуты"
cap("set.system", A, "Настройки системы и документа", ["ISystemSettings", "IDocumentSettings"], GW)
cap("set.styles", A, "Стили линий, штриховок, текстов", ["IStylesManager", "ICurveStyle", "IHatchStyle"], GW)
cap("set.attributes", A, "Атрибуты объектов и документов", ["ksAttributeObject", "IAttribute"], GW)

PRIO_ORDER = {"частые": 0, "": 1}

# Проба 2026-10-19 (ILibraryManager.ProceduresLibraries / DocumentsLibraries): имя, подключено, зачем
INSTALLED_LIBS = [
    ("Стандартные изделия (ПОЛИНОМ)", True, "крепёж/подшипники ГОСТ — частые; COM API нет, .NET Ascon.Polynom.Api.dll — исследовать"),
    ("Материалы и сортаменты (ПОЛИНОМ)", True, "материал детали из справочника"),
    ("Модели крепежных элементов (constr3d.kle)", True, "старая библиотека крепежа — документ-библиотека, доступна через IInsert"),
    ("Валы и механические передачи 3D / 3D+2D", True, "валы (эталон чертежей — вал); интерактивно"),
    ("Разъемные соединения", True, "болтовые соединения в модели — частые; интерактивно"),
    ("Механика: Пружины", True, "редко"),
    ("Оборудование: Трубопроводы", True, "вакуумные линии — возможно"),
    ("Оборудование: Металлоконструкции", True, "рамы стендов — возможно"),
    ("Авторасстановка позиций", True, "сборочный чертёж — позиции"),
    ("Проверка документа", True, "нормоконтроль чертежей"),
    ("Неразъемные соединения / в модели; Условные изображения швов", True, "сварка вакуумных узлов"),
    ("Сервисные инструменты; КОМПАС-Макро; Рецензент; Покрытия; Развертки; Кабельные каналы; "
     "Вентиляция; Конвертер единиц; IOSO-K", True, "вне задачи"),
    ("Размерные цепи; Каталоги (муфты, двигатели, редукторы); Анимация; Кабели и жгуты; eCAD; "
     "Свободная форма; Штампы/Пресс-формы; Распознавание 3D; APM FEM; KompasFlow", False, "не подключены; расчёты — не в этот раз"),
    ("Библиотеки документов: конструктивные элементы, типовые тексты, фигуры и знаки, электротехнические, "
     "типовые элементы, технологические обозначения", True, "фрагменты для чертежей"),
]


def _first(pat, text):
    m = re.search(pat, text)
    return m.group(1).strip() if m else ""


SDK_ZIP = os.environ.get("KOMPAS_SDK_HELP_ZIP", r"C:\Program Files\ASCON\SDK\Help\KOMPAS_SDK_ru-RU.zip")


def sdk_v23_types() -> dict:
    """Таблицы типов из справки SDK v23 (stubs — v22, в них нет 22 новых типов модели):
    {константа: (название, api5, api7)}. Нет zip — пустой словарь."""
    import html
    import zipfile
    out = {}
    if not os.path.exists(SDK_ZIP):
        return out
    z = zipfile.ZipFile(SDK_ZIP)
    for page, (ic, iname, i5, i7) in (("obj3dtype.html", (0, 2, 3, 4)),
                                      ("objtypes.html", (1, 0, 6, 7))):
        s = z.read(page).decode("utf-8", "replace")
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
            c = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))).strip()
                 for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            if len(c) > max(ic, iname, i5, i7) and re.match(r"^(o3d_|ks)\w+$", c[ic]):
                fix = lambda t: re.sub(r"\bI (?=[A-Z])", "I", t)    # «I DrawingTable» в html справки
                out[c[ic]] = (c[iname], fix(c[i5]), fix(c[i7]))
    return out


def _guess_api7(idx, const):
    """o3d_FaceMover → IFaceMover, ksDrConicCurve4Point → IConicCurve…; '' если не нашлось."""
    base = re.sub(r"^(o3d_|ksDr|ks)", "", const)
    base = base[:1].upper() + base[1:]
    for cand in (base, base + "3D", re.sub(r"\d+Point$", "", base), base.replace("sheetMetal", "SheetMetal"),
                 "SheetMetal" + base[len("SheetMetal"):] if base.lower().startswith("sheetmetal") else ""):
        if cand and "I" + cand in idx["classes"]:
            return "I" + cand
    return ""


def enum_rows(idx):
    rows = []
    sdk23 = sdk_v23_types()
    for enum, areas, kind in (("ksObj3dTypeEnum", AREAS_3D, "3d"), ("DrawingObjectTypeEnum", AREAS_2D, "2d")):
        for name, (val, doc) in idx["enums"][enum]["values"].items():
            if kind == "2d" and val <= 0:
                continue
            area = next(a for a, rx in areas if re.search(rx, name))
            if kind == "3d":
                func = _first(r"Название объекта:\s*(.+)", doc)
                api5 = _first(r"API 5:\s*(.+)", doc)
                api7 = _first(r"API 7:\s*(.+)", doc)
            else:
                func = _first(r"Название объекта:\s*(.+)", doc)
                api5 = _first(r"Интерфейс API5:\s*(.+)", doc)
                api7 = _first(r"Интерфейс API7:\s*(.+)", doc) or _first(r"Интерфейс параметров:\s*(.+)", doc)
            v23 = sdk23.get(name)
            if v23:
                func = v23[0] or func
                api5 = v23[1] or api5
                api7 = v23[2] or api7
            tool, note = COVER.get(name, ("", ""))
            if not api7 and not api5:
                api7 = _guess_api7(idx, name)
                if api7:
                    note = (note + "; " if note else "") + "интерфейс подобран по имени типа — в справке не указан"
            rows.append({"key": name, "area": area, "func": func or "(описания нет в справке SDK)",
                         "const": val, "api5": api5, "api7": api7,
                         "mcp": tool or (GW if (api7 or api5) and "служебные" not in area else ""),
                         "note": note, "service": "служебные" in area, "prio": ""})
    return rows


def check_names(idx, rows):
    """Каждое имя интерфейса/члена в описи должно существовать в индексе."""
    bad = []
    for r in rows:
        for ref in r.get("api", []):
            cls, _, mem = ref.partition(".")
            if cls not in idx["classes"]:
                bad.append(f"{r['key']}: нет интерфейса {cls}")
            elif mem and api_index.member(idx, cls, mem) is None:
                bad.append(f"{r['key']}: нет члена {ref} (похожие: {api_index.similar(list(api_index.members(idx, cls)['methods']) + list(api_index.members(idx, cls)['props']), mem)})")
    return bad


def status(row, checks):
    ch = checks.get(row["key"])
    if row["mcp"] and row["mcp"] != GW:
        st = "есть"
    elif row["mcp"] == GW:
        st = "шлюз"
    else:
        st = "нет"
    return st, (f"{ch['result']} {ch.get('date', '')}".strip() if ch else "")


def main(argv):
    idx = api_index.load()
    curated = [dict(r) for r in C]
    bad = check_names(idx, curated)
    if bad:
        print("ОШИБКИ имён в описи:\n  " + "\n  ".join(bad))
        if "--check" in argv or "--strict" in argv:
            return 1
    if "--check" in argv:
        print(f"OK: {len(curated)} ручных строк, имена сверены")
        return 0
    try:
        with open(CHECKS, encoding="utf-8") as f:
            checks = json.load(f)
    except FileNotFoundError:
        checks = {}
    rows = enum_rows(idx) + curated
    for r in rows:
        r["status"], r["checked"] = status(r, checks)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated_by": "tools/capabilities.py", "rows": rows}, f,
                  ensure_ascii=False, indent=1)
    write_md(rows)
    print(f"Записано: {OUT_JSON}, {OUT_MD} — {len(rows)} строк")
    return 0


def write_md(rows):
    func_rows = [r for r in rows if not r.get("service")]
    by_area = {}
    for r in func_rows:
        by_area.setdefault(r["area"], []).append(r)
    tot = {s: sum(1 for r in func_rows if r["status"] == s) for s in ("есть", "шлюз", "нет")}
    L = ["# Опись возможностей КОМПАС-3D v23 для MCP kompas3d", "",
         "Сгенерировано `tools/capabilities.py` — **не править вручную**: ручной слой — вызовы `cap(...)` "
         "в генераторе, живые проверки — `docs/kompas-capabilities.checks.json`. Машиночитаемо — `kompas-capabilities.json`.", "",
         "Источники: справка КОМПАС (оглавление 3162 раздела, `C:\\Program Files\\ASCON\\Help\\KOMPAS_ru-RU.zip`), "
         "справка SDK (stubs v22, `ksObj3dTypeEnum` / `DrawingObjectTypeEnum` — соответствие API5↔API7), "
         "библиотеки типов v23 (`kAPI7.tlb`, `kAPI5.tlb`, `ksConstants*.tlb`).", "",
         "Статус: **есть** — свой инструмент MCP; **шлюз** — интерфейс в API есть, доступ только через универсальный "
         f"шлюз `{GW}` (план); **нет** — интерфейса параметров нет ни в справке SDK v23, ни в библиотеке типов (объект читается только как IModelObject: имя, видимость), либо функция вне этой задачи. «частые» — кандидаты в отдельные инструменты.", "",
         f"Итого функций: {len(func_rows)} — есть {tot['есть']}, шлюз {tot['шлюз']}, нет {tot['нет']}. "
         f"Служебных типов API (коллекции, параметры) — {len(rows) - len(func_rows)}, в JSON.", "",
         "| Раздел | функций | есть | шлюз | нет |", "|---|---|---|---|---|"]
    for a, rs in by_area.items():
        L.append(f"| {a} | {len(rs)} | {sum(r['status'] == 'есть' for r in rs)} | "
                 f"{sum(r['status'] == 'шлюз' for r in rs)} | {sum(r['status'] == 'нет' for r in rs)} |")
    for a, rs in by_area.items():
        L += ["", f"## {a}", "", "| Функция | API (API7 / API5) | MCP | Статус | Проверено | Примечание |",
              "|---|---|---|---|---|---|"]
        rs = sorted(rs, key=lambda r: (PRIO_ORDER.get(r["prio"], 1), r["status"] != "есть"))
        for r in rs:
            api = ", ".join(r["api"]) if "api" in r else " / ".join(x for x in (r.get("api7"), r.get("api5")) if x)
            key = f" `{r['key']}`"
            prio = " **частые**" if r["prio"] else ""
            L.append(f"| {r['func']}{key} | {api} | {r['mcp']} | {r['status']}{prio} | {r['checked']} | {r['note']} |")
    L += ["", "## Приложение: установленные библиотеки (живая проба 2026-10-19, ILibraryManager)", "",
          "Команды прикладных библиотек запускаются `ILibrary.Execute(команда)` и открывают ДИАЛОГИ — "
          "без участия пользователя работают только в связке с автонажатием (`kompas_auto_dialogs`), и то не все. "
          "Собственного COM API у приложений в SDK нет, кроме документов-библиотек (.kle) и библиотек моделей (.l3d).", "",
          "| Приложение | Подключено | Отношение к задачам пользователя |", "|---|---|---|"]
    for name, att, why in INSTALLED_LIBS:
        L.append(f"| {name} | {'да' if att else 'нет'} | {why} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
