cmake_minimum_required(VERSION 3.20)
project(RiveShader LANGUAGES CXX OBJCXX)

# Third-party dependencies live outside the synced drive (see build.sh): the Adobe AE SDK (your own download)
# and the wgpu-native release (step c).
set(AE_SDK "$ENV{HOME}/.cache/ae-plugin-deps/AfterEffectsSDK" CACHE PATH "After Effects SDK root (contains Examples/Headers)")
set(WGPU_NATIVE "$ENV{HOME}/.cache/ae-plugin-deps/wgpu-native" CACHE PATH "wgpu-native release root (include/, lib/)")
set(AE_SDK_HEADERS "${AE_SDK}/Examples/Headers")
set(AE_SDK_UTIL "${AE_SDK}/Examples/Util")
set(AE_SDK_RESOURCES "${AE_SDK}/Examples/Resources")
if(NOT EXISTS "${AE_SDK_HEADERS}/AE_Effect.h")
    message(FATAL_ERROR "AE SDK not found at ${AE_SDK} (expected ${AE_SDK_HEADERS}/AE_Effect.h)")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_ARCHITECTURES arm64)
set(CMAKE_OSX_DEPLOYMENT_TARGET 12.0)

set(RS_SOURCES src/RiveShader.cpp src/RsRegistry.cpp src/RsDialog.mm)
add_library(RiveShader MODULE ${RS_SOURCES})
set_source_files_properties(src/RsDialog.mm PROPERTIES COMPILE_FLAGS "-fobjc-arc")
target_include_directories(RiveShader PRIVATE src "${AE_SDK_HEADERS}" "${AE_SDK_HEADERS}/SP" "${AE_SDK_UTIL}" "${AE_SDK_HEADERS}/adobesdk")
# AE_OS_MAC / AE_PROC_ARM64 come from AEConfig.h (TargetConditionals + __arm64__)
target_compile_options(RiveShader PRIVATE -fvisibility=hidden -Wall -Wno-unused-parameter)
# only the two AE entry points leave the bundle (wgpu-native's static lib would otherwise export ~6000 symbols)
target_link_options(RiveShader PRIVATE "-Wl,-exported_symbols_list,${CMAKE_CURRENT_SOURCE_DIR}/src/exports.txt" "-Wl,-dead_strip")
set_target_properties(RiveShader PROPERTIES
    BUNDLE TRUE
    BUNDLE_EXTENSION "plugin"
    OUTPUT_NAME "RiveShader"
    MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in"
    MACOSX_BUNDLE_EXECUTABLE_NAME "RiveShader"
    MACOSX_BUNDLE_BUNDLE_NAME "RiveShader"
    MACOSX_BUNDLE_GUI_IDENTIFIER "app.rive.aftereffects.RiveShader"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "0.1.0"
    MACOSX_BUNDLE_BUNDLE_VERSION "1")

# flagcheck: host tool that prints the PiPL constants derived from RiveShader.h
add_executable(flagcheck src/flagcheck.cpp)
target_include_directories(flagcheck PRIVATE src "${AE_SDK_HEADERS}" "${AE_SDK_HEADERS}/SP" "${AE_SDK_UTIL}")


# ---- GPU module (wgpu-native, static) shared by the plugin and the rs_apply CLI
if(NOT EXISTS "${WGPU_NATIVE}/lib/libwgpu_native.a")
    message(FATAL_ERROR "wgpu-native not found at ${WGPU_NATIVE}")
endif()
add_library(rsgpu STATIC src/RsShader.cpp src/RsGpu.cpp)
target_include_directories(rsgpu PUBLIC src "${WGPU_NATIVE}/include")
target_link_libraries(rsgpu PUBLIC "${WGPU_NATIVE}/lib/libwgpu_native.a"
    "-framework Metal" "-framework QuartzCore" "-framework Foundation" "-framework CoreFoundation" iconv objc)
set_target_properties(rsgpu PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_executable(rs_apply tools/rs_apply.cpp)
target_link_libraries(rs_apply PRIVATE rsgpu)

target_link_libraries(RiveShader PRIVATE rsgpu "-framework AppKit" "-framework UniformTypeIdentifiers")
