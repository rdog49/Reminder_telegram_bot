# jira-digest Specification

## Purpose

Собирает одно сводное сообщение слота: базовый текст из `message.json` и список незакрытых задач Jira с исполнителями и пингами Telegram из `users_map.json`.

## Requirements

### Requirement: Message is a header plus issues
Текст отправки SHALL начинаться с непустого поля `text` из `message.json`. Ниже SHALL идти список задач в порядке выборки Jira. Если в `message.json` задан непустой `parse_mode`, отправка MUST использовать его.

#### Scenario: Header stays first
- **WHEN** `message.json` содержит текст «Сводка дня», а Jira возвращает задачу `PROJ-1`
- **THEN** отправленный текст начинается с «Сводка дня», и ниже есть строка про `PROJ-1`

#### Scenario: Parse mode is kept
- **WHEN** `message.json` содержит `parse_mode` со значением `HTML`
- **THEN** сообщение уходит в Telegram с `parse_mode` равным `HTML`

### Requirement: Open issues of one project
Система SHALL запрашивать у `JIRA_URL` задачи по JQL `project = <JIRA_BOARD_PROJECT_KEY> AND status = "In Progress" ORDER BY created DESC`, входя как `JIRA_USER` с `JIRA_API_TOKEN`. В список MUST попадать только задачи этого проекта в статусе In Progress.

#### Scenario: In-progress issue is included
- **WHEN** в проекте есть задача со статусом In Progress
- **THEN** эта задача входит в сводку

#### Scenario: Other status is excluded
- **WHEN** в проекте есть задача со статусом To Do или Done
- **THEN** этой задачи в сводке нет

#### Scenario: Another project is excluded
- **WHEN** задача в статусе In Progress принадлежит другому проекту
- **THEN** этой задачи в сводке нет

### Requirement: Issue line names the assignee
Строка задачи SHALL содержать ключ, название, ссылку на задачу и исполнителя. Если отображаемое имя исполнителя точно совпадает с ключом в `users_map.json`, строка MUST содержать значение этого ключа как пинг Telegram.

#### Scenario: Mapped assignee is pinged
- **WHEN** исполнитель задачи отображается как «Иван Иванов», а в `users_map.json` есть ключ «Иван Иванов» со значением `@ivan`
- **THEN** строка задачи содержит ключ задачи, её название и `@ivan`

#### Scenario: Unknown assignee is not pinged
- **WHEN** отображаемого имени исполнителя нет в `users_map.json`
- **THEN** строка показывает это имя и не содержит пинг из карты

#### Scenario: Unassigned issue has no ping
- **WHEN** у задачи нет исполнителя
- **THEN** строка помечает задачу как без исполнителя и не содержит пинг

### Requirement: Empty query still reminds
Если выборка Jira не содержит задач, система SHALL отправить базовый текст и явную строку, что незакрытых задач нет.

#### Scenario: No open issues
- **WHEN** Jira возвращает пустой список
- **THEN** в Telegram уходит базовый текст и строка, что незакрытых задач нет

### Requirement: Sources are read for each slot
`message.json` и `users_map.json` SHALL читаться заново при каждой отправке слота. Изменение файла MUST действовать на следующую отправку без перезапуска процесса.

#### Scenario: Edited header is used next
- **WHEN** текст в `message.json` изменён после старта процесса и наступает новый слот
- **THEN** новое сообщение содержит уже новый текст

### Requirement: Map is a name-to-mention object
`users_map.json` SHALL быть JSON-объектом. Ключ SHALL быть отображаемым именем в Jira, значение SHALL быть строкой пинга Telegram.

#### Scenario: Valid map is applied
- **WHEN** файл содержит объект `{ "Иван Иванов": "@ivan" }`
- **THEN** исполнитель с этим именем упоминается как `@ivan`

### Requirement: Digest failure sends nothing
Система MUST NOT отправлять сообщение слота, если не хватает `JIRA_URL`, `JIRA_USER`, `JIRA_API_TOKEN` или `JIRA_BOARD_PROJECT_KEY`, если `message.json` или `users_map.json` нельзя прочитать как описанный документ, либо если Jira отвечает ошибкой. При отсутствии настроек Jira процесс MUST завершиться с ошибкой до первой отправки.

#### Scenario: Jira settings are missing
- **WHEN** при старте не задан `JIRA_API_TOKEN`
- **THEN** процесс завершается с ошибкой и не отправляет сообщение

#### Scenario: Jira is unavailable
- **WHEN** слот наступил, а Jira отвечает ошибкой
- **THEN** сообщение в Telegram не отправляется

#### Scenario: Map file is invalid
- **WHEN** `users_map.json` не является JSON-объектом имён и пингов
- **THEN** сообщение в Telegram не отправляется

### Requirement: One message fits Telegram
Система SHALL отправить не больше одного сообщения на слот. Если полный список не помещается в лимит Telegram, сообщение MUST остаться в этом лимите, сохранить заголовок и закончиться явной пометкой, что показаны не все задачи.

#### Scenario: Long list is cut off
- **WHEN** список задач длиннее лимита одного сообщения Telegram
- **THEN** уходит одно сообщение с заголовком, началом списка и пометкой, что список неполный

### Requirement: Secrets stay out of logs
Процесс MUST NOT записывать в лог значения `JIRA_API_TOKEN` и `BOT_TOKEN`.

#### Scenario: Jira error is logged without the token
- **WHEN** запрос к Jira завершается ошибкой
- **THEN** в логе есть факт ошибки и нет значения `JIRA_API_TOKEN`
