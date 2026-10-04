// Prints the numeric values the PiPL must carry (out flags, version) — run by build.sh.
#include "AEConfig.h"
#include "AE_Effect.h"
#include "RiveShader.h"
#include <cstdio>
int main() {
    std::printf("RS_OUT_FLAGS=%lu\nRS_OUT_FLAGS2=%lu\nRS_VERSION=%lu\n", (unsigned long)(RS_OUT_FLAGS), (unsigned long)(RS_OUT_FLAGS2),
                (unsigned long)PF_VERSION(RS_MAJOR_VERSION, RS_MINOR_VERSION, RS_BUG_VERSION, RS_STAGE_VERSION, RS_BUILD_VERSION));
    std::printf("RS_NAME=%s\nRS_MATCH_NAME=%s\nRS_CATEGORY=%s\nRS_SUPPORT_URL=%s\n", RS_NAME, RS_MATCH_NAME, RS_CATEGORY, RS_SUPPORT_URL);
    return 0;
}
