// Portable protocol/state machine. No Arduino dependencies; host regression tested.
#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

struct C2Packet {
  char session[17];
  uint32_t seq, epoch;
  char op[8];
  int throttle, steering;
};

inline bool c2uint(const char* s, uint32_t& out) {
  if (!*s) return false;
  for (const char* p=s; *p; ++p) if (*p<'0' || *p>'9') return false;
  errno=0; char* end;
  unsigned long long n=strtoull(s, &end, 10);
  if (errno || *end || n>0xffffffffULL) return false;
  out=(uint32_t)n; return true;
}
inline bool c2int(const char* s, int& out) {
  const char* p=s; if (*p=='-') ++p;
  if (!*p) return false;
  for (; *p; ++p) if (*p<'0' || *p>'9') return false;
  errno=0; char* end; long n=strtol(s, &end, 10);
  if (errno || *end || n < -255 || n > 255) return false;
  out=(int)n; return true;
}
inline bool c2parse(const char* data, size_t size, C2Packet& out) {
  if (!size || size>=128 || memchr(data, '\0', size)) return false;
  char buf[128]; memcpy(buf,data,size); buf[size]=0;
  if (size && buf[size-1]=='\n') buf[--size]=0;
  if (size && buf[size-1]=='\r') buf[--size]=0;
  char* fields[7]; fields[0]=buf; int count=1;
  for (size_t i=0; i<size; ++i) if (buf[i]==',') {
    if (count>=7) return false;
    buf[i]=0; fields[count++]=buf+i+1;
  }
  if (count!=7 || strcmp(fields[0],"C2V1") || strlen(fields[1])!=16 || strlen(fields[4])>7) return false;
  for (const char* p=fields[1]; *p; ++p) if (!((*p>='0' && *p<='9') || (*p>='a' && *p<='f'))) return false;
  if (!c2uint(fields[2],out.seq) || !out.seq || !c2uint(fields[3],out.epoch) ||
      !c2int(fields[5],out.throttle) || !c2int(fields[6],out.steering)) return false;
  const char* valid[]={"HELLO","PING","ARM","DRIVE","DISARM","STOP","RESET"};
  bool known=false; for (auto op:valid) if (!strcmp(op,fields[4])) known=true;
  if (!known) return false;
  if (strcmp(fields[4],"DRIVE") && (out.throttle || out.steering)) return false;
  // Match the initial 25% wheel cap, including throttle/steering mixing.
  if (abs(out.throttle+out.steering)>64 || abs(out.throttle-out.steering)>64) return false;
  strcpy(out.session,fields[1]); strcpy(out.op,fields[4]); return true;
}

class C2Guard {
 public:
  char session[17] = "";
  uint32_t seq=0, epoch=1, lastDrive=0, lastOwner=0;
  bool enabled=false, stopped=false;
  int throttle=0, steering=0;
  const char* reason="STARTUP";
  static const uint32_t timeoutMs=250;
  void disable(const char* why, bool latch=false) {
    // Invalidate outstanding ARM/DRIVE packets on a state transition.
    if (enabled || (latch && !stopped)) { ++epoch; if (!epoch) ++epoch; }
    enabled=false; throttle=steering=0; stopped=stopped||latch; reason=why;
  }
  void tick(uint32_t now) {
    if (enabled && uint32_t(now-lastDrive)>=timeoutMs) disable("COMMAND_TIMEOUT");
  }
  bool accept(const C2Packet& p, uint32_t now) {
    tick(now);
    if (!strcmp(p.op,"HELLO")) {
      // An active owner cannot be replaced while driving or sending fresh traffic.
      if (strcmp(session,p.session)) {
        if (enabled || (*session && uint32_t(now-lastOwner)<1000)) return false;
        strcpy(session,p.session); seq=0; ++epoch; if (!epoch) ++epoch;
      }
    } else if (strcmp(session,p.session) || !*session) return false;
    if (p.seq<=seq) return false;
    if (!strcmp(p.op,"ARM")) {
      if (stopped || enabled || p.epoch!=epoch) return false;
      enabled=true; lastDrive=now; reason="ENABLED";
    } else if (!strcmp(p.op,"DRIVE")) {
      if (!enabled || stopped || p.epoch!=epoch) return false;
      throttle=p.throttle; steering=p.steering; lastDrive=now; reason="DRIVING";
    } else if (!strcmp(p.op,"STOP")) disable("OPERATOR_STOP",true);
    else if (!strcmp(p.op,"DISARM")) disable("DISARMED");
    else if (!strcmp(p.op,"RESET")) {
      if (enabled || p.epoch!=epoch) return false;
      stopped=false; ++epoch; if (!epoch) ++epoch; reason="RESET_DISABLED";
    }
    // PING and HELLO acknowledge communication but NEVER feed the drive watchdog.
    seq=p.seq; lastOwner=now; return true;
  }
};
