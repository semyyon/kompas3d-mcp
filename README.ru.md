# kompas3d-mcp

[![CI](https://github.com/semyyon/kompas3d-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/semyyon/kompas3d-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

MCP-сервер, который позволяет ИИ-ассистенту (Claude Desktop, Claude Code и любому другому MCP-клиенту)
управлять **КОМПАС-3D** через COM API: открывать и сохранять документы, строить детали, собирать и
перемещать компоненты, измерять модели и создавать размеренные чертежи.

> English version: [README.md](README.md)

> **Неофициальный проект.** КОМПАС-3D — продукт и товарный знак АСКОН. Репозиторий не связан с АСКОН
> и не содержит кода или файлов SDK АСКОН.

## Для кого

Для конструкторов и инженеров по автоматизации САПР, которые уже работают в КОМПАС-3D и хотят передать
ассистенту рутину (вставить 50 крепежей, проверить состав изделия, сделать первый вариант чертежа).
Каждый результат проверяется числами (габарит, масса, число тел), а не принимается на слово.

## Требования

- Windows 10/11
- КОМПАС-3D **v23** (единственная версия, на которой разрабатывалось и проверялось)
- Python 3.10 и новее (разработка на 3.12)
- Пакеты из [requirements.txt](requirements.txt): `mcp` (1.x), `pywin32`, `Pillow`, `numpy`, `jsonschema`

Сервер обращается к запущенному КОМПАС через COM, поэтому работает на той же машине Windows и под тем же
пользователем, что и КОМПАС.

## Установка

```powershell
git clone https://github.com/semyyon/kompas3d-mcp.git
cd kompas3d-mcp
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Проверка, что сервер запускается (КОМПАС он сам не стартует):

```powershell
.venv\Scripts\python -c "import server; print(server.kompas_status())"
```

## Подключение к MCP-клиенту

Любому клиенту нужна команда, запускающая `server.py` по stdio. Пути абсолютные; в примерах репозиторий
лежит в `C:\tools\kompas3d-mcp`.

**Claude Desktop** — `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "kompas3d": {
      "command": "C:\\tools\\kompas3d-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\tools\\kompas3d-mcp\\server.py"]
    }
  }
}
```

**Claude Code**:

```powershell
claude mcp add kompas3d -- C:\tools\kompas3d-mcp\.venv\Scripts\python.exe C:\tools\kompas3d-mcp\server.py
```

**Другие клиенты** (Cursor, Cline и т. п.) принимают ту же пару `command` / `args` в настройках MCP.

Необязательные переменные окружения:

| Переменная | Назначение |
|---|---|
| `KOMPAS_BIN` | Папка с библиотеками типов КОМПАС (по умолчанию `C:\Program Files\ASCON\Bin`) |
| `KOMPAS_SDK_HELP_ZIP` | Путь к архиву справки SDK, включает офлайн-поиск `kompas_sdk_search` |
| `KOMPAS_STUBS_DIR` | Папка со stub-файлами SDK (`.pyi`) для подсказок `kompas_api` (в репозитории не поставляются) |
| `KOMPAS_RULES_MD` | Markdown-список пунктов визуальной проверки чертежа (не поставляется) |

## Инструменты

| Инструмент | Что делает |
|---|---|
| `kompas_status` | Запущен ли КОМПАС, список открытых документов (сам его не запускает) |
| `kompas_launch` | Запускает КОМПАС или подключается к запущенному |
| `kompas_create_document` | Новый документ: деталь, сборка, чертёж, фрагмент, спецификация, текст |
| `kompas_open_document` | Открывает `.m3d`, `.a3d`, `.cdw` и другие файлы КОМПАС |
| `kompas_save_document` | Save / Save As; расширения `.step`, `.stl`, `.pdf`, `.dxf` дают конвертацию |
| `kompas_close_document` | Закрывает документ по пути или части имени |
| `kompas_backup` | Файловая копия `<имя>_backup.<расш.>` рядом с документом |
| `kompas_screenshot` | PNG-скриншот окна КОМПАС |
| `kompas_view` | Ставит стандартные виды и снимает их одним вызовом |
| `kompas_export_raster` | Чистый рендер 3D-модели в PNG/JPG/BMP |
| `kompas_auto_dialogs` | Автоответ на всплывающие диалоги КОМПАС, которые блокируют COM |
| `kompas_get_tree` | Дерево сборки с мировыми origin и группировкой одинаковых |
| `kompas_get_bbox` | Габарит документа или компонента, найденного по имени |
| `kompas_measure` | Числовой самоконтроль: габарит, масса, грани, рёбра, тела, свойства |
| `kompas_insert` | Вставка компонентов в сборку внешними ссылками, партиями со сверкой |
| `kompas_move` | Перемещение компонентов верхнего уровня с проверкой смещения |
| `kompas_part_props` | Имя, обозначение, материал и цвет детали |
| `kompas_shape` | Одна твёрдотельная операция (основание, бобышка, вырез, вращение) по эскизу в JSON |
| `kompas_feature` | Фаска, скругление или условная резьба с выбором рёбер по точкам |
| `kompas_variables` | Чтение, добавление и задание переменных модели с перестроением и сверкой |
| `kompas_audit_fasteners` | Группировка и подсчёт крепежа сборки, проверка зацепления |
| `kompas_interference` | Реальные пересечения тел (а не габаритов) |
| `kompas_bom` | Состав изделия сборки (только чтение) |
| `kompas_model_summary` | Сводка геометрии детали для планирования чертежа (только чтение) |
| `kompas_snapshot` | Компактный JSON-снимок чертежа: штамп, виды, размеры |
| `kompas_place_dimensions` | Размеры на виде: модель решает ЧТО образмерить, код — ГДЕ |
| `kompas_check_drawing` | Проверка открытого чертежа по правилам проекта и ЕСКД |
| `kompas_build_drawing` | Весь чертёж детали по `drawing_spec.json` одним вызовом; сохранение только при пройденных проверках |
| `kompas_api` | Шлюз ко всему COM API (API7 и API5) без скрипта, с проверкой имён |
| `kompas_sdk_search` | Офлайн-поиск по справке SDK (интерфейсы, методы, свойства) |
| `kompas_run_job` | Запуск именованного скрипта из `jobs/` с параметрами JSON |
| `kompas_run_script` | Произвольный Python против живого COM API (крайнее средство) |
| `kompas_batch` | Цепочка вызовов `kompas_*` за один заход под одной блокировкой |

## Примеры запросов

- «Открой `Bracket.m3d`, задай материал Сталь 12Х18Н10Т и доложи габарит, массу и число тел».
- «Вставь `M6x20.m3d` 24 раза в открытую сборку по этим координатам и покажи аудит крепежа».
- «Проверь сборку на пересечения тел и перечисли пары».
- «Построй чертёж `Plate.m3d` по этому spec и исправь всё, что покажет `kompas_check_drawing`».
- «Каким методом KOMPAS API задаётся глубина выдавливания? Найди в справке SDK».

## Как устроено

- `server.py` — сервер [FastMCP](https://github.com/modelcontextprotocol/python-sdk) по stdio.
- К КОМПАС подключается через `pywin32` по обоим COM API: API7 (`IApplication`) и API5 (`KompasObject`).
- Экземпляр КОМПАС один, поэтому доступ защищён межпроцессной блокировкой;
  `kompas_batch` держит её на всю цепочку.
- Изменяющие инструменты принимают `document=`, чтобы правка не ушла в документ, который КОМПАС сделал активным сам.
- `jobs/` — именованные скрипты для `kompas_run_job`; `jobs/_draw.py` — движок 2D-чертежей.
- Чертежи задаются JSON-spec ([схема](schemas/drawing_spec.schema.json), [примеры](schemas/examples));
  проверки в `drawing_rules.py`, настраиваемые пороги и типовые тексты — в `project_rules.json`.
- `hooks/` — необязательные хуки Claude Code, например запрет сохранять сборку без свежей резервной копии.
- Каждый вызов замеряется и пишется в `logs/calls.jsonl`.

## Ограничения

- Только Windows и КОМПАС-3D v23. Другие версии могут работать, но не проверялись.
- Описания инструментов частично на русском, правила чертежей следуют ЕСКД/ГОСТ.
  В `project_rules.json` — значения автора; под свои стандарты их нужно править.
- Stub-файлы SDK и архив справки принадлежат АСКОН и не включены. Без них `kompas_api` опирается на
  установленные библиотеки типов, а для `kompas_sdk_search` нужен `KOMPAS_SDK_HELP_ZIP`.
- Некоторые операции КОМПАС открывают модальные диалоги, блокирующие COM; см. `kompas_auto_dialogs`.
- `kompas_run_script` выполняет произвольный код с правами вашего пользователя. Подключайте только доверенные клиенты.

## Разработка

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Тесты не требуют КОМПАС; те, которым он нужен, пропускаются автоматически. См. [CONTRIBUTING.md](CONTRIBUTING.md).

## Благодарности

[Model Context Protocol](https://modelcontextprotocol.io) и его Python SDK, [pywin32](https://github.com/mhammond/pywin32).
Разработано вместе с Claude.

## Лицензия

[MIT](LICENSE)
