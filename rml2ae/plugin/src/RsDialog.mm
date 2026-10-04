#import <AppKit/AppKit.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#include "RsDialog.h"

namespace rs {
std::string pickWgslFile() {
    @autoreleasepool {
        NSOpenPanel* panel = [NSOpenPanel openPanel];
        panel.title = @"Choose a Rive .wgsl shader";
        panel.canChooseFiles = YES;
        panel.canChooseDirectories = NO;
        panel.allowsMultipleSelection = NO;
        if (@available(macOS 11.0, *)) {
            UTType* wgsl = [UTType typeWithFilenameExtension:@"wgsl"];
            panel.allowedContentTypes = wgsl ? @[wgsl] : @[];
        }
        if ([panel runModal] != NSModalResponseOK) return "";
        NSURL* url = panel.URLs.firstObject;
        return url ? std::string(url.path.UTF8String) : std::string();
    }
}
}
