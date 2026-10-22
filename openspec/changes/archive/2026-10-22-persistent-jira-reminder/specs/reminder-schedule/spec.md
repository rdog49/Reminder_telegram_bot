# Spec Delta

## Purpose

Держит напоминание постоянно запущенным и отправляет не больше одного сообщения на каждый час из `SEND_HOURS` в часовом поясе `TZ`, догоняя пропущенный слот только в те же календарные сутки.

## ADDED Requirements

### Requirement: Process stays running
Процесс SHALL оставаться запущенным после старта до сигнала остановки. Одна успешная или неуспешная отправка MUST NOT завершать процесс.

#### Scenario: Success does not exit
- **WHEN** слот успешно отправлен
- **THEN** процесс продолжает работать и ждёт следующий слот

#### Scenario: Failure does not exit
- **WHEN** попытка отправки слота завершается ошибкой
- **THEN** процесс продолжает работать

#### Scenario: Stop signal ends the process
- **WHEN** процесс получает сигнал остановки
- **THEN** он завершается и больше не отправляет сообщения

### Requirement: Send at configured hours
Система SHALL отправлять сообщение в минуту 00 каждого часа из `SEND_HOURS`, считая время в часовом поясе `TZ`. Система MUST NOT проверять слоты по поминутному или иному короткому тикеру.

#### Scenario: Hour matches the schedule
- **WHEN** локальное время в `TZ` достигает 19:00, час 19 входит в `SEND_HOURS`, а слот этого часа ещё не записан
- **THEN** система отправляет ровно одно сообщение этого слота

#### Scenario: Hour is outside the schedule
- **WHEN** наступает час, которого нет в `SEND_HOURS`
- **THEN** система не отправляет сообщение за этот час

#### Scenario: Future hour of today waits
- **WHEN** процесс запущен в 10:00 по `TZ`, а ближайший час из `SEND_HOURS` — 19
- **THEN** система не отправляет сообщение до 19:00 этих суток

### Requirement: Slot identity
Система SHALL обозначать слот ключом `YYYY-MM-DDTHH` из локальной даты и часа в `TZ`. Успешная отправка MUST записать этот ключ в `data/sent.json`. Уже записанный ключ MUST NOT быть отправлен повторно.

#### Scenario: Successful send is recorded
- **WHEN** отправка слота `2026-10-22T19` проходит успешно
- **THEN** `data/sent.json` содержит ключ `2026-10-22T19`

#### Scenario: Recorded slot is skipped
- **WHEN** ключ `2026-10-22T19` уже есть в `data/sent.json`
- **THEN** система не отправляет второе сообщение этого слота

### Requirement: Same-day catch-up only
Если процесс стартует после начала часа из `SEND_HOURS` текущих локальных суток и этот слот не записан, система SHALL отправить его один раз. Слот с локальной датой раньше текущей MUST NOT отправляться.

#### Scenario: Missed hour is sent later the same day
- **WHEN** процесс стартует в 20:30 по `TZ`, час 19 входит в `SEND_HOURS`, а слот 19 этих суток не записан
- **THEN** система один раз отправляет слот 19

#### Scenario: Several missed hours of today are each sent once
- **WHEN** процесс стартует в 22:00 по `TZ`, часы 19, 20 и 21 входят в `SEND_HOURS` и ни один из этих слотов текущих суток не записан
- **THEN** система отправляет каждый из трёх слотов один раз, по возрастанию часа

#### Scenario: Previous day is not backfilled
- **WHEN** процесс стартует на следующие локальные сутки, а вчерашний слот 19 не был записан
- **THEN** система не отправляет вчерашний слот и ждёт ближайший час из `SEND_HOURS` текущих суток

### Requirement: Hours stay independent
Каждый час из `SEND_HOURS` SHALL быть отдельным слотом. Отправка одного часа MUST NOT отменять неотправленный более поздний час тех же суток.

#### Scenario: Later hour still fires after catch-up
- **WHEN** слот 19 текущих суток уже отправлен, а час 21 входит в `SEND_HOURS` и его слот не записан
- **THEN** в 21:00 система отправляет слот 21

### Requirement: Failed delivery is not recorded
Если сообщение слота не удалось собрать или не удалось доставить, система MUST NOT записывать ключ слота.

#### Scenario: Delivery error leaves the slot open
- **WHEN** попытка отправки слота завершается ошибкой
- **THEN** ключа этого слота нет в `data/sent.json`, и повтор в те же сутки ещё возможен

### Requirement: Required schedule settings
Процесс MUST завершиться с ошибкой и ничего не отправить, если нет `BOT_TOKEN`, `CHAT_ID` или `TZ`, часовой пояс неизвестен, либо `SEND_HOURS` не является списком часов от 0 до 23.

#### Scenario: Timezone is missing
- **WHEN** переменная `TZ` пуста
- **THEN** процесс завершается с ошибкой и не отправляет сообщение

#### Scenario: Send hours are invalid
- **WHEN** `SEND_HOURS` содержит значение вне диапазона 0–23
- **THEN** процесс завершается с ошибкой и не отправляет сообщение
