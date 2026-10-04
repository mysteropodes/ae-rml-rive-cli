cmake_minimum_required(VERSION 3.20)
project(RiveShader LANGUAGES CXX)
if(APPLE)
    enable_language(OBJCXX)
elseif(WIN32)
    enable_language(RC)
endif()

# Third-party dependencies live outside the repository (see build.sh / build.ps1): the Adobe AE SDK (your own
# download) and the wgpu-native release (downloaded and sha256-checked by the build script).
if(WIN32)
    file(TO_CMAKE_PATH "$ENV{LOCALAPPDATA}/ae-plugin-deps" RS_DEPS)
else()
    set(RS_DEPS "$ENV{HOME}/.cache/ae-plugin-deps")
endif()
set(AE_SDK "${RS_DEPS}/AfterEffectsSDK" CACHE PATH "After Effects SDK root (contains Examples/Headers)")
set(WGPU_NATIVE "${RS_DEPS}/wgpu-native" CACHE PATH "wgpu-native release root (include/, lib/)")
# GPU module + rs_apply only: no After Effects SDK needed (CI, and checking a machine's GPU before building the plugin)
option(RS_GPU_ONLY "Build only the GPU module and rs_apply" OFF)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(APPLE)
    set(CMAKE_OSX_ARCHITECTURES arm64)
    set(CMAKE_OSX_DEPLOYMENT_TARGET 12.0)
endif()
if(MSVC)
    # sources are UTF-8 (labels such as "Load .wgsl…"); windows.h must not define min/max over std::min/max
    add_compile_options(/utf-8 /W3)
    add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN _CRT_SECURE_NO_WARNINGS)
endif()


# ---- GPU module (wgpu-native, static) shared by the plugin and the rs_apply CLI
if(APPLE)
    set(WGPU_LIB "${WGPU_NATIVE}/lib/libwgpu_native.a")
    set(WGPU_SYSTEM_LIBS "-framework Metal" "-framework QuartzCore" "-framework Foundation" "-framework CoreFoundation" iconv objc)
elseif(WIN32)
    set(WGPU_LIB "${WGPU_NATIVE}/lib/wgpu_native.lib")
    # what wgpu-native's static library needs from Windows (its own examples link the same list)
    set(WGPU_SYSTEM_LIBS d3dcompiler ws2_32 userenv bcrypt ntdll opengl32 propsys runtimeobject)
else()
    # Linux: rs_apply only (Vulkan), e.g. against the library shipped with the Python wgpu package
    if(EXISTS "${WGPU_NATIVE}/lib/libwgpu_native.a")
        set(WGPU_LIB "${WGPU_NATIVE}/lib/libwgpu_native.a")
    else()
        set(WGPU_LIB "${WGPU_NATIVE}/lib/libwgpu_native.so")
    endif()
    set(WGPU_SYSTEM_LIBS dl pthread m)
endif()
if(NOT EXISTS "${WGPU_LIB}")
    message(FATAL_ERROR "wgpu-native not found at ${WGPU_NATIVE} (expected ${WGPU_LIB})")
endif()
add_library(rsgpu STATIC src/RsShader.cpp src/RsGpu.cpp)
target_include_directories(rsgpu PUBLIC src "${WGPU_NATIVE}/include")
target_link_libraries(rsgpu PUBLIC "${WGPU_LIB}" ${WGPU_SYSTEM_LIBS})
set_target_properties(rsgpu PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_executable(rs_apply tools/rs_apply.cpp)
target_link_libraries(rs_apply PRIVATE rsgpu)

# ---- host side that needs no After Effects SDK (shader registry, log, file picker): built in RS_GPU_ONLY mode too,
# so the CI compiles the Windows code without the SDK
if(APPLE)
    add_library(rshost STATIC src/RsRegistry.cpp src/RsDialog.mm)
    set_source_files_properties(src/RsDialog.mm PROPERTIES COMPILE_FLAGS "-fobjc-arc")
    target_link_libraries(rshost PUBLIC "-framework AppKit" "-framework UniformTypeIdentifiers")
elseif(WIN32)
    add_library(rshost STATIC src/RsRegistry.cpp src/RsDialog_win.cpp)
    target_link_libraries(rshost PUBLIC ole32 shell32)
else()
    add_library(rshost STATIC src/RsRegistry.cpp)
endif()
target_include_directories(rshost PUBLIC src)
set_target_properties(rshost PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(RS_GPU_ONLY)
    return()
endif()


# ---- the After Effects plugin
set(AE_SDK_HEADERS "${AE_SDK}/Examples/Headers")
set(AE_SDK_UTIL "${AE_SDK}/Examples/Util")
set(AE_SDK_RESOURCES "${AE_SDK}/Examples/Resources")
if(NOT EXISTS "${AE_SDK_HEADERS}/AE_Effect.h")
    message(FATAL_ERROR "AE SDK not found at ${AE_SDK} (expected ${AE_SDK_HEADERS}/AE_Effect.h)")
endif()
set(AE_SDK_INCLUDES "${AE_SDK_HEADERS}" "${AE_SDK_HEADERS}/SP" "${AE_SDK_UTIL}" "${AE_SDK_HEADERS}/adobesdk")

# flagcheck: host tool that prints the PiPL constants derived from RiveShader.h
add_executable(flagcheck src/flagcheck.cpp)
target_include_directories(flagcheck PRIVATE src ${AE_SDK_INCLUDES})

if(APPLE)
    add_library(RiveShader MODULE src/RiveShader.cpp)
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
    target_link_libraries(RiveShader PRIVATE rshost rsgpu)
    # the PiPL (Rez) and the signature are done by build.sh
elseif(WIN32)
    # PiPL: RiveShaderPiPL.r.in + the flags printed by flagcheck -> the SDK's cl /EP -> PiPLTool -> cl /EP chain -> .rc
    find_program(PIPLTOOL NAMES PiPLtool PiPLTool HINTS "${AE_SDK_RESOURCES}" NO_DEFAULT_PATH REQUIRED)
    set(PIPL_RC "${CMAKE_CURRENT_BINARY_DIR}/RiveShaderPiPL.rc")
    add_custom_command(OUTPUT "${PIPL_RC}"
        COMMAND "${CMAKE_COMMAND}"
            "-DFLAGCHECK=$<TARGET_FILE:flagcheck>" "-DTEMPLATE=${CMAKE_CURRENT_SOURCE_DIR}/src/RiveShaderPiPL.r.in"
            "-DCL=${CMAKE_CXX_COMPILER}" "-DPIPLTOOL=${PIPLTOOL}" "-DINCLUDES=${AE_SDK_INCLUDES};${AE_SDK_RESOURCES}"
            "-DWORK=${CMAKE_CURRENT_BINARY_DIR}" "-DOUT=${PIPL_RC}"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/pipl_win.cmake"
        DEPENDS flagcheck src/RiveShaderPiPL.r.in src/RiveShader.h pipl_win.cmake
        COMMENT "PiPL resource (RiveShaderPiPL.rc)")
    add_library(RiveShader MODULE src/RiveShader.cpp "${PIPL_RC}")
    set_target_properties(RiveShader PROPERTIES OUTPUT_NAME "RiveShader" SUFFIX ".aex" PREFIX "")
    # EffectMain / PluginDataEntryFunction2 are exported by the SDK's DllExport (__declspec(dllexport))
    target_link_libraries(RiveShader PRIVATE rshost rsgpu)
else()
    message(FATAL_ERROR "the After Effects plugin builds on macOS and Windows only (use -DRS_GPU_ONLY=ON here)")
endif()
target_include_directories(RiveShader PRIVATE src ${AE_SDK_INCLUDES})
