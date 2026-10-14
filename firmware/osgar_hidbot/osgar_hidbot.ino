#include <Arduino.h>
#include <Keyboard.h>

static const uint8_t PIN_GREEN  = 2;
static const uint8_t PIN_RED    = 3;
static const uint8_t PIN_LED_K1 = 4;
static const uint8_t PIN_LED_K2 = 5;
static const uint8_t PIN_BUTTON = 6;
static const uint8_t PIN_SYNC   = 7;

static const uint8_t PROTO = 8;
static const uint8_t CHANNELS = 2;
static const uint8_t RING_SIZE = 96;

static const uint16_t SERIAL_BAUD = 115200;
static const uint8_t LINE_MAX = 48;

static uint8_t cfgKey1 = 0x1D;
static uint8_t cfgKey2 = 0x1B;
static uint16_t cfgLeadMs     = 0;
static uint16_t cfgMinPulseMs = 1;
static uint16_t cfgWatchdogMs = 500;
static uint16_t cfgTickMs     = 200;
static bool     cfgVerbose    = false;

static const uint16_t PRE_GAP_MIN_MS  = 80;
static const uint16_t PRE_GAP_MAX_MS  = 450;
static const uint16_t MARK_GAP_MIN_MS = 550;
static const uint16_t MARK_GAP_MAX_MS = 900;
static const uint8_t  PRE_EDGES_MIN   = 2;
static const uint8_t  SYNC_MISS_LIMIT = 3;

static const uint16_t BUTTON_DEBOUNCE_MS = 40;
static const uint16_t LED_FLASH_MS = 25;

struct Event {
  int32_t tMs;
  uint8_t key;
  uint8_t action;
};

static Event ring[RING_SIZE];
static uint8_t ringHead = 0;
static uint8_t ringCount = 0;

static bool ringPush(int32_t tMs, uint8_t key, uint8_t action) {
  if (ringCount >= RING_SIZE) return false;
  ring[(ringHead + ringCount) % RING_SIZE] = {tMs, key, action};
  ringCount++;
  return true;
}

static void ringClear() {
  ringHead = 0;
  ringCount = 0;
}

static int32_t  pathMs = 0;
static uint32_t lastAdvanceUs = 0;
static uint16_t fracUs = 0;

static bool armed = false;
static bool running = false;
static bool errorFlag = false;
static bool calibrating = false;
static uint32_t lastHostMs = 0;
static uint32_t firedCount = 0;

static void clockReset(int32_t toMs) {
  pathMs = toMs;
  lastAdvanceUs = micros();
  fracUs = 0;
}

static void clockAdvance() {
  uint32_t now = micros();
  uint32_t delta = now - lastAdvanceUs;
  lastAdvanceUs = now;
  fracUs += (uint16_t)(delta % 1000);
  pathMs += (int32_t)(delta / 1000);
  if (fracUs >= 1000) {
    fracUs -= 1000;
    pathMs++;
  }
}

static bool keyDown[CHANNELS] = {false, false};
static uint32_t keyLedUntil[CHANNELS] = {0, 0};
static uint8_t previewMode = 0;
static bool previewLed[CHANNELS] = {false, false};

static uint8_t usageFor(uint8_t key) {
  return key == 1 ? cfgKey1 : cfgKey2;
}

static const uint8_t HID_RAW_OFFSET = 136;
static const uint8_t HID_MAX_RAW_USAGE = 255 - HID_RAW_OFFSET;
static const uint8_t HID_MOD_FIRST = 0xE0;
static const uint8_t HID_MOD_LAST = 0xE7;
static const uint8_t HID_MOD_OFFSET = 128;

static bool usageSupported(uint8_t usage) {
  if (usage >= HID_MOD_FIRST && usage <= HID_MOD_LAST) return true;
  return usage >= 0x04 && usage <= HID_MAX_RAW_USAGE;
}

static inline uint8_t hidCode(uint8_t usage) {
  if (usage >= HID_MOD_FIRST && usage <= HID_MOD_LAST) {
    return HID_MOD_OFFSET + (usage - HID_MOD_FIRST);
  }
  return usage + HID_RAW_OFFSET;
}

static uint8_t ledPinFor(uint8_t i) {
  return i == 0 ? PIN_LED_K1 : PIN_LED_K2;
}

static void keyPress(uint8_t key) {
  uint8_t i = key - 1;
  if (i >= CHANNELS || keyDown[i]) return;
  Keyboard.press(hidCode(usageFor(key)));
  keyDown[i] = true;
  digitalWrite(ledPinFor(i), HIGH);
  keyLedUntil[i] = millis() + LED_FLASH_MS;
}

static void keyRelease(uint8_t key) {
  uint8_t i = key - 1;
  if (i >= CHANNELS || !keyDown[i]) return;
  Keyboard.release(hidCode(usageFor(key)));
  keyDown[i] = false;
  if (keyLedUntil[i] < millis()) keyLedUntil[i] = millis();
}

static void keysAllUp() {
  for (uint8_t k = 1; k <= CHANNELS; k++) keyRelease(k);
  Keyboard.releaseAll();
}

static volatile uint32_t syncEdgeUs = 0;
static volatile uint8_t  syncEdgeSeq = 0;
static uint8_t syncSeenSeq = 0;

static uint32_t prevEdgeUs = 0;
static uint8_t  fastEdgeRun = 0;
static bool     syncLocked = false;
static int32_t  tickIndex = -1;
static uint16_t missedTicks = 0;
static uint32_t lastTickMs = 0;
static uint32_t calibEdges = 0;

static void syncIsr() {
  syncEdgeUs = micros();
  syncEdgeSeq++;
}

static char line[LINE_MAX];
static uint8_t lineLen = 0;

static void sendIdent() {
  Serial.print(F("OSGARHID "));
  Serial.print(PROTO);
  Serial.print(' ');
  Serial.print(CHANNELS);
  Serial.print(' ');
  Serial.println(RING_SIZE);
}

static void sendStat() {
  Serial.print(F("STAT armed="));
  Serial.print(armed ? 1 : 0);
  Serial.print(F(" running="));
  Serial.print(running ? 1 : 0);
  Serial.print(F(" t="));
  Serial.print(pathMs);
  Serial.print(F(" q="));
  Serial.print(ringCount);
  Serial.print(F(" free="));
  Serial.print(RING_SIZE - ringCount);
  Serial.print(F(" lock="));
  Serial.print(syncLocked ? 1 : 0);
  Serial.print(F(" tick="));
  Serial.print(tickIndex);
  Serial.print(F(" missed="));
  Serial.print(missedTicks);
  Serial.print(F(" fired="));
  Serial.print(firedCount);
  Serial.print(F(" err="));
  Serial.print(errorFlag ? 1 : 0);
  Serial.print(F(" prv="));
  Serial.print(previewMode);
  Serial.print(F(" cal="));
  Serial.print(calibrating ? 1 : 0);
  Serial.print(F(" edges="));
  Serial.print(calibEdges);
  Serial.print(F(" pin="));
  Serial.print(digitalRead(PIN_SYNC));
  Serial.print(F(" k1="));
  Serial.print(keyDown[0] ? 1 : 0);
  Serial.print(F(" k2="));
  Serial.println(keyDown[1] ? 1 : 0);
}

static void fail(const __FlashStringHelper *why) {
  errorFlag = true;
  Serial.print(F("ERR "));
  Serial.println(why);
}

static uint8_t tokenize(char *s, char *out[], uint8_t maxTokens) {
  uint8_t n = 0;
  char *p = s;
  while (*p && n < maxTokens) {
    while (*p == ' ' || *p == '\t') p++;
    if (!*p) break;
    out[n++] = p;
    while (*p && *p != ' ' && *p != '\t') p++;
    if (*p) *p++ = '\0';
  }
  return n;
}

static void stopAll(bool alsoClear) {
  keysAllUp();
  running = false;
  armed = false;
  calibrating = false;
  syncLocked = false;
  fastEdgeRun = 0;
  tickIndex = -1;
  if (alsoClear) ringClear();
}

static void applyConfig(const char *key, long value) {
  if (value < 0) value = 0;
  if (!strcmp(key, "key1") || !strcmp(key, "key2")) {
    uint8_t usage = (uint8_t)min(value, 255L);
    if (!usageSupported(usage)) {
      fail(F("HID usage not reachable: use 0x04-0x77 or 0xE0-0xE7"));
      return;
    }
    if (key[3] == '1') cfgKey1 = usage; else cfgKey2 = usage;
  }
  else if (!strcmp(key, "lead")) cfgLeadMs = (uint16_t)min(value, 200L);
  else if (!strcmp(key, "minpulse")) cfgMinPulseMs = (uint16_t)min(value, 500L);
  else if (!strcmp(key, "wdog")) cfgWatchdogMs = (uint16_t)min(value, 10000L);
  else if (!strcmp(key, "tick")) cfgTickMs = (uint16_t)constrain(value, 100L, 5000L);
  else if (!strcmp(key, "verbose")) cfgVerbose = value != 0;
  else {
    fail(F("unknown CFG key"));
    return;
  }
  Serial.print(F("OK CFG "));
  Serial.print(key);
  Serial.print(' ');
  Serial.println(value);
}

static void handleLine(char *s) {
  char *tok[6];
  uint8_t n = tokenize(s, tok, 6);
  if (n == 0) return;

  lastHostMs = millis();
  const char *cmd = tok[0];

  if (!strcmp(cmd, "HELLO")) { sendIdent(); return; }
  if (!strcmp(cmd, "HB")) return;
  if (!strcmp(cmd, "STATUS")) { sendStat(); return; }
  if (!strcmp(cmd, "PING")) {
    Serial.print(F("PONG "));
    Serial.println(n > 1 ? tok[1] : "");
    return;
  }
  if (!strcmp(cmd, "CFG")) {
    if (n < 3) { fail(F("CFG needs a key and a value")); return; }
    applyConfig(tok[1], atol(tok[2]));
    return;
  }
  if (!strcmp(cmd, "CLEAR")) {
    stopAll(true);
    errorFlag = false;
    calibEdges = 0;
    Serial.println(F("OK CLEAR"));
    return;
  }
  if (!strcmp(cmd, "EV")) {
    if (n < 4) { fail(F("EV needs t, key, action")); return; }
    uint8_t key = (uint8_t)atoi(tok[2]);
    if (key < 1 || key > CHANNELS) { fail(F("EV key out of range")); return; }
    if (!ringPush((int32_t)atol(tok[1]), key, atoi(tok[3]) ? 1 : 0)) {
      fail(F("FULL"));
      return;
    }
    Serial.print(F("OK EV "));
    Serial.print(ringCount);
    Serial.print(' ');
    Serial.println(RING_SIZE - ringCount);
    return;
  }
  if (!strcmp(cmd, "ARM")) {
    armed = true;
    running = false;
    calibrating = false;
    errorFlag = false;
    syncLocked = false;
    fastEdgeRun = 0;
    tickIndex = -1;
    missedTicks = 0;
    firedCount = 0;
    clockReset(0);
    Serial.println(F("OK ARM"));
    return;
  }
  if (!strcmp(cmd, "START")) {
    if (!armed) { fail(F("not armed")); return; }
    clockReset(n > 1 ? (int32_t)atol(tok[1]) : 0);
    running = true;
    lastTickMs = millis();
    Serial.println(F("OK START"));
    return;
  }
  if (!strcmp(cmd, "SYNC")) {
    if (n < 2) { fail(F("SYNC needs a path time")); return; }
    int32_t want = (int32_t)atol(tok[1]);
    if (!syncLocked) {
      int32_t err = want - pathMs;
      clockReset(want);
      if (!running && armed) { running = true; lastTickMs = millis(); }
      Serial.print(F("OK SYNC "));
      Serial.println(err);
    } else {
      Serial.println(F("OK SYNC ignored"));
    }
    return;
  }
  if (!strcmp(cmd, "STOP")) {
    stopAll(true);
    Serial.println(F("OK STOP"));
    return;
  }
  if (!strcmp(cmd, "TAP")) {
    if (n < 3) { fail(F("TAP needs a key and a duration")); return; }
    uint8_t key = (uint8_t)atoi(tok[1]);
    if (key < 1 || key > CHANNELS) { fail(F("TAP key out of range")); return; }
    uint16_t ms = (uint16_t)max(atoi(tok[2]), (int)cfgMinPulseMs);
    keyPress(key);
    delay(ms);
    keyRelease(key);
    Serial.print(F("OK TAP "));
    Serial.print(key);
    Serial.print(' ');
    Serial.println(ms);
    return;
  }
  if (!strcmp(cmd, "PRV")) {
    previewMode = n > 1 ? (uint8_t)constrain(atol(tok[1]), 0L, 2L) : 0;
    previewLed[0] = false;
    previewLed[1] = false;
    if (previewMode < 2) keysAllUp();
    Serial.print(F("OK PRV "));
    Serial.println(previewMode);
    return;
  }
  if (!strcmp(cmd, "LED")) {
    if (n < 3) { fail(F("LED needs two states")); return; }
    bool a = atoi(tok[1]) != 0;
    bool b = atoi(tok[2]) != 0;
    previewLed[0] = a;
    previewLed[1] = b;
    if (previewMode >= 2) {
      if (a) keyPress(1); else keyRelease(1);
      if (b) keyPress(2); else keyRelease(2);
    }
    return;
  }
  if (!strcmp(cmd, "CAL")) {
    calibrating = !calibrating;
    calibEdges = 0;
    fastEdgeRun = 0;
    Serial.print(F("OK CAL "));
    Serial.println(calibrating ? 1 : 0);
    return;
  }

  fail(F("unknown command"));
}

static void serialService() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = '\0';
      if (lineLen) handleLine(line);
      lineLen = 0;
      continue;
    }
    if (lineLen < LINE_MAX - 1) {
      line[lineLen++] = c;
    } else {
      lineLen = 0;
      fail(F("line too long"));
    }
  }
}

static void syncService() {
  uint8_t seq = syncEdgeSeq;
  if (seq != syncSeenSeq) {
    uint32_t edgeUs = syncEdgeUs;
    syncSeenSeq = seq;
    uint32_t gapMs = (edgeUs - prevEdgeUs) / 1000u;
    prevEdgeUs = edgeUs;

    if (calibrating) {
      calibEdges++;
      Serial.print(F("CALEDGE "));
      Serial.print(calibEdges);
      Serial.print(' ');
      Serial.println(gapMs);
      return;
    }

    if (gapMs >= PRE_GAP_MIN_MS && gapMs <= PRE_GAP_MAX_MS) {
      if (fastEdgeRun < 255) fastEdgeRun++;
      return;
    }

    if (!syncLocked && fastEdgeRun >= PRE_EDGES_MIN &&
        gapMs >= MARK_GAP_MIN_MS && gapMs <= MARK_GAP_MAX_MS) {
      fastEdgeRun = 0;
      tickIndex = 0;
      syncLocked = true;
      missedTicks = 0;
      clockReset(0);
      running = armed;
      lastTickMs = millis();
      Serial.println(F("SYNCFOUND"));
      return;
    }

    if (syncLocked) {
      uint16_t tick = cfgTickMs ? cfgTickMs : 1;
      int32_t steps = (int32_t)((gapMs + tick / 2) / tick);
      if (steps < 1) steps = 1;
      if (steps > 1) missedTicks += (uint16_t)(steps - 1);
      tickIndex += steps;
      int32_t authoritative = tickIndex * (int32_t)tick;
      clockReset(authoritative);
      lastTickMs = millis();
      Serial.print(F("TICK "));
      Serial.print(tickIndex);
      Serial.print(' ');
      Serial.println(authoritative);
      return;
    }

    fastEdgeRun = 0;
  }

  if (syncLocked && running) {
    uint32_t quietMs = millis() - lastTickMs;
    if (quietMs > (uint32_t)cfgTickMs * SYNC_MISS_LIMIT) {
      syncLocked = false;
      errorFlag = true;
      Serial.print(F("SYNCLOST "));
      Serial.println(quietMs / (cfgTickMs ? cfgTickMs : 1));
    }
  }
}

static void scheduleService() {
  if (!running) return;
  while (ringCount > 0) {
    const Event &e = ring[ringHead];
    int32_t due = e.action ? (e.tMs - (int32_t)cfgLeadMs) : e.tMs;
    if (pathMs < due) break;

    if (e.action) keyPress(e.key); else keyRelease(e.key);
    firedCount++;
    if (cfgVerbose) {
      Serial.print(F("FIRE "));
      Serial.print(e.key);
      Serial.print(' ');
      Serial.print(e.action);
      Serial.print(' ');
      Serial.println(e.tMs);
    }
    ringHead = (ringHead + 1) % RING_SIZE;
    ringCount--;
  }
}

static bool buttonDown = false;
static uint32_t buttonChangedMs = 0;

static void buttonService() {
  bool down = digitalRead(PIN_BUTTON) == LOW;
  uint32_t now = millis();
  if (down == buttonDown) return;
  if (now - buttonChangedMs < BUTTON_DEBOUNCE_MS) return;
  buttonChangedMs = now;
  buttonDown = down;

  Serial.print(F("BTN "));
  Serial.println(down ? 1 : 0);

  if (down) {
    stopAll(true);
    errorFlag = false;
    calibEdges = 0;
    Serial.println(F("OK RESET"));
  }
}

static void watchdogService() {
  if (!armed && !running) return;
  if (cfgWatchdogMs == 0) return;
  if (millis() - lastHostMs <= cfgWatchdogMs) return;
  stopAll(true);
  errorFlag = true;
  Serial.print(F("ERR host silent for over "));
  Serial.print(cfgWatchdogMs);
  Serial.println(F(" ms, keys released"));
}

static void ledService() {
  uint32_t now = millis();

  if (previewMode && (now - lastHostMs) > 2000) {
    previewLed[0] = false;
    previewLed[1] = false;
    if (previewMode >= 2) keysAllUp();
  }

  for (uint8_t i = 0; i < CHANNELS; i++) {
    bool on = previewMode
              ? previewLed[i]
              : (keyDown[i] || (int32_t)(keyLedUntil[i] - now) > 0);
    digitalWrite(ledPinFor(i), on ? HIGH : LOW);
  }

  digitalWrite(PIN_RED, armed ? HIGH : LOW);
  digitalWrite(PIN_GREEN, running ? HIGH : LOW);
}

void setup() {
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_LED_K1, OUTPUT);
  pinMode(PIN_LED_K2, OUTPUT);
  digitalWrite(PIN_GREEN, LOW);
  digitalWrite(PIN_RED, LOW);
  digitalWrite(PIN_LED_K1, LOW);
  digitalWrite(PIN_LED_K2, LOW);

  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_SYNC, INPUT_PULLUP);

  Serial.begin(SERIAL_BAUD);
  Keyboard.begin();

  attachInterrupt(digitalPinToInterrupt(PIN_SYNC), syncIsr, CHANGE);

  clockReset(0);
  lastHostMs = millis();
  prevEdgeUs = micros();

  digitalWrite(PIN_GREEN, HIGH);
  digitalWrite(PIN_RED, HIGH);
  digitalWrite(PIN_LED_K1, HIGH);
  digitalWrite(PIN_LED_K2, HIGH);
  delay(250);
  digitalWrite(PIN_GREEN, LOW);
  digitalWrite(PIN_RED, LOW);
  digitalWrite(PIN_LED_K1, LOW);
  digitalWrite(PIN_LED_K2, LOW);
}

void loop() {
  clockAdvance();
  serialService();
  syncService();
  scheduleService();
  buttonService();
  watchdogService();
  ledService();
}
