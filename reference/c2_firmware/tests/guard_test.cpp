#include "../mini_rover_c2/C2Guard.h"
#include <cassert>
#include <cstdio>
#include <string>

bool parse(const std::string& s) { C2Packet p; return c2parse(s.data(),s.size(),p); }
C2Packet command(const char* op, uint32_t seq, uint32_t epoch, int t=0, int s=0) {
  char text[128];
  snprintf(text,sizeof(text),"C2V1,0123456789abcdef,%lu,%lu,%s,%d,%d\n",(unsigned long)seq,(unsigned long)epoch,op,t,s);
  C2Packet p; assert(c2parse(text,strlen(text),p)); return p;
}
int main() {
  for (const char* bad : {"", "MODE:TELEOP", "255,255", "C2V1,0123456789abcdef,0,1,ARM,0,0",
       "C2V1,0123456789abcdef,4294967296,1,ARM,0,0", "C2V1,0123456789abcdef,1,1,ARM,1,0",
       "C2V1,0123456789abcdef,1,1,DRIVE,50,50", "C2V1,0123456789abcdef,1,1,DRIVE,nan,0",
       "C2V1,0123456789abcdef,1,1,DRIVE,9999999999999999999999,0",
       "C2V1,0123456789abcdef,1,1,DRIVE,1.0,0", "C2V1,0123456789abcdef,1,1,ARM,0,0,extra"}) assert(!parse(bad));
  const char embeddedNul[]="C2V1\0,0123456789abcdef,1,1,ARM,0,0";
  assert(!parse(std::string(embeddedNul,sizeof(embeddedNul)-1)));
  assert(parse("C2V1,0123456789abcdef,1,1,DRIVE,-32,32\r\n"));
  C2Guard g;
  assert(!g.accept(command("ARM",1,1),0));
  assert(g.accept(command("HELLO",1,0),0));
  auto epoch=g.epoch;
  assert(!g.enabled);
  assert(g.accept(command("ARM",2,epoch),10));
  assert(g.enabled && !g.throttle);
  assert(g.accept(command("DRIVE",3,epoch,32,16),20));
  assert(g.throttle==32);
  assert(!g.accept(command("DRIVE",3,epoch,0,0),30));
  assert(g.accept(command("PING",4,epoch),250));
  g.tick(270); assert(!g.enabled && !g.throttle && g.epoch!=epoch);
  assert(!g.accept(command("DRIVE",5,epoch,32,0),271));
  assert(!g.accept(command("ARM",6,epoch),272));
  assert(g.accept(command("ARM",7,g.epoch),273));
  // Expiry is checked before an arriving DRIVE can revive motion.
  assert(!g.accept(command("DRIVE",8,g.epoch,32,0),523));
  assert(!g.enabled);
  assert(g.accept(command("STOP",9,g.epoch),524));
  assert(g.stopped);
  assert(!g.accept(command("ARM",10,g.epoch),525));
  assert(g.accept(command("RESET",11,g.epoch),526));
  assert(!g.stopped && !g.enabled);
  assert(g.accept(command("ARM",12,g.epoch),527));
  assert(g.accept(command("DISARM",13,g.epoch),528));
  assert(!g.enabled);
  auto other=command("HELLO",1,0); strcpy(other.session,"aaaaaaaaaaaaaaaa");
  assert(!g.accept(other,600));
  assert(g.accept(other,1528));
  assert(!g.accept(command("ARM",14,g.epoch),1529));
  C2Guard wrap;
  assert(wrap.accept(command("HELLO",1,0),0xffffff00U));
  assert(wrap.accept(command("ARM",2,wrap.epoch),0xffffff80U));
  wrap.tick(121); assert(wrap.enabled);
  wrap.tick(122); assert(!wrap.enabled);
  puts("C2Guard parser, ordering, watchdog, STOP, ownership and millis rollover checks passed.");
}
