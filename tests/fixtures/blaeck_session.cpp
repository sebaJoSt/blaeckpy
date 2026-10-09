#include <Blaeck.h>
#include <cstdio>
#include <string>

class Port : public Stream
{
public:
  std::string in, out;
  size_t pos = 0;
  int available() override { return (int)(in.size() - pos); }
  int read() override { return pos < in.size() ? (unsigned char)in[pos++] : -1; }
  int peek() override { return pos < in.size() ? (unsigned char)in[pos] : -1; }
  size_t write(uint8_t b) override { out.push_back((char)b); return 1; }
};

static Blaeck device;
static Port port;

static void dump(const char *label)
{
  // One line per frame: label, then the frame's bytes in hex, markers to LF.
  size_t start = 0;
  while (start < port.out.size())
  {
    size_t end = port.out.find("/>\n", start);
    if (end == std::string::npos) { printf("%s partial\n", label); break; }
    end += 3;
    printf("%s ", label);
    for (size_t i = start; i < end; i++) printf("%02X", (unsigned char)port.out[i]);
    printf("\n");
    start = end;
  }
  port.out.clear();
}

static void command(const char *text)
{
  port.in = text; port.pos = 0;
  device.tick();
  dump(text);
}

float temperature = 21.5f; long long count = 7; char label[] = "hi"; bool flag = true;
float flow = 1.25f;
float setpoint = 20.0f; bool heater = false; byte mode = 1; char note[33] = "a<b";
float room = 19.75f;
static void printStatus() {}

int main()
{
  device.begin(port);
  device.withName(F("Greenhouse"));
  device.withFWVersion(F("1.0"));
  device.addSignal(F("Temperature"), &temperature);
  device.addSignal(F("Count"), &count);
  device.addSignal(F("Label"), label);
  device.addSignal(F("Flag"), &flag);
  BlaeckDeviceRef pump = device.addDevice(F("Pump controller")).withHWVersion(F("Simulated")).withFWVersion(F("1.0"));
  pump.addSignal(F("Flow"), &flow);
  pump.addEvent(F("Alarms"), F("restarted"));
  device.addNumberInput(F("Setpoint"), &setpoint).withRange(10.0f, 30.0f, 0.5f).withUnit(F("\xC2\xB0" "C")).withMode(BLAECK_NUMBER_MODE_SLIDER);
  device.addSwitch(F("Heater"), &heater);
  device.addSelect(F("Mode"), &mode, F("Auto,Manual"));
  device.addTextInput(F("Note"), note, sizeof(note));
  device.addSensor(F("Room"), &room).withDisplayName(F("Room air")).withIcon(F("mdi:home")).withDeviceClass(F("temperature")).withStateClass(BLAECK_STATE_CLASS_MEASUREMENT).withDisplayPrecision(1);
  device.addEvent(F("Alarms"), F("Overheat,Dry run")).withDisplayName(F("Alarm")).config();
  device.addButton("PrintStatus", printStatus).withIcon(F("mdi:printer"));

  command("<#7:BLAECK.GET_DEVICES>");
  command("<BLAECK.WRITE_ENTITIES>");
  command("<BLAECK.DATA_START>");
  command("<BLAECK.WRITE_DATA>");
  command("<BLAECK.ENTITIES_START>");
  command("<Setpoint,25>");
  device.writeEvent("Alarms", "Dry run");
  dump("event");
  command("<Nope,1>");
  return 0;
}
