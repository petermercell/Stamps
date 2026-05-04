# CMakeLists.txt - Stamps C++ NDK Plugin
#
# Build with:
#   mkdir build && cd build
#   cmake .. -DNUKE_INSTALL_PATH=/path/to/Nuke14.1v8
#   make -j$(nproc)

cmake_minimum_required(VERSION 3.15)
project(StampsCpp VERSION 2.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# -----------------------------------------------------------------
# Nuke NDK path
# -----------------------------------------------------------------
if(NOT NUKE_INSTALL_PATH)
    # Try common locations
    if(EXISTS "$ENV{HOME}/Nuke14.1v8")
        set(NUKE_INSTALL_PATH "$ENV{HOME}/Nuke14.1v8")
    elseif(EXISTS "/usr/local/Nuke14.1v8")
        set(NUKE_INSTALL_PATH "/usr/local/Nuke14.1v8")
    elseif(EXISTS "$ENV{NUKE_PATH}")
        set(NUKE_INSTALL_PATH "$ENV{NUKE_PATH}")
    else()
        message(FATAL_ERROR
            "NUKE_INSTALL_PATH not set. Use:\n"
            "  cmake .. -DNUKE_INSTALL_PATH=/path/to/NukeXX.Xv*\n"
            "Or set the NUKE_PATH environment variable.")
    endif()
endif()

message(STATUS "Nuke install path: ${NUKE_INSTALL_PATH}")

# -----------------------------------------------------------------
# Nuke NDK includes and libraries
# -----------------------------------------------------------------
set(NUKE_INCLUDE_DIR "${NUKE_INSTALL_PATH}/include")
if(NOT EXISTS "${NUKE_INCLUDE_DIR}/DDImage/Iop.h")
    message(FATAL_ERROR
        "Cannot find Nuke NDK headers at ${NUKE_INCLUDE_DIR}\n"
        "Make sure NUKE_INSTALL_PATH points to a valid Nuke installation.")
endif()

# Find the DDImage library
find_library(DDIMAGE_LIB
    NAMES DDImage
    PATHS "${NUKE_INSTALL_PATH}"
    NO_DEFAULT_PATH
)
if(NOT DDIMAGE_LIB)
    message(FATAL_ERROR "Cannot find DDImage library in ${NUKE_INSTALL_PATH}")
endif()
message(STATUS "DDImage library: ${DDIMAGE_LIB}")

# -----------------------------------------------------------------
# Platform-specific flags
# -----------------------------------------------------------------
if(UNIX AND NOT APPLE)
    # Linux
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fPIC -msse")
    set(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--no-undefined")
    add_definitions(-DUSE_GLEW -DFNPLUGINCLASS_H)
elseif(APPLE)
    # macOS
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fPIC")
    add_definitions(-DUSE_GLEW -DFNPLUGINCLASS_H)
    set(CMAKE_SHARED_MODULE_SUFFIX ".dylib")
elseif(WIN32)
    # Windows
    add_definitions(-DFNPLUGINCLASS_H -DWIN32 -D_WIN32 -DWINDOWS)
endif()

# Suppress deprecation warnings from NDK headers
add_definitions(-D_SILENCE_ALL_CXX17_DEPRECATION_WARNINGS)

# -----------------------------------------------------------------
# Source files
# -----------------------------------------------------------------
set(SOURCES
    src/StampAnchor.cpp
    src/StampWired.cpp
    src/StampDeep.cpp
)

set(HEADERS
    src/StampCommon.h
)

# -----------------------------------------------------------------
# Build the shared library (plugin)
# -----------------------------------------------------------------
add_library(StampsCpp SHARED ${SOURCES} ${HEADERS})

target_include_directories(StampsCpp PRIVATE
    ${NUKE_INCLUDE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(StampsCpp PRIVATE
    ${DDIMAGE_LIB}
)

# Platform-specific output name / extension
if(WIN32)
    set_target_properties(StampsCpp PROPERTIES
        SUFFIX ".dll"
        PREFIX ""
    )
elseif(APPLE)
    set_target_properties(StampsCpp PROPERTIES
        SUFFIX ".dylib"
        PREFIX ""
    )
else()
    set_target_properties(StampsCpp PROPERTIES
        SUFFIX ".so"
        PREFIX ""
    )
endif()

# Strip debug symbols for release builds
if(CMAKE_BUILD_TYPE STREQUAL "Release")
    if(UNIX)
        add_custom_command(TARGET StampsCpp POST_BUILD
            COMMAND strip $<TARGET_FILE:StampsCpp>
        )
    endif()
endif()

# -----------------------------------------------------------------
# Install
# -----------------------------------------------------------------
# Default install to ~/.nuke
set(STAMPS_INSTALL_DIR "$ENV{HOME}/.nuke/stamps_cpp"
    CACHE PATH "Installation directory for stamps_cpp plugin")

install(TARGETS StampsCpp
    LIBRARY DESTINATION "${STAMPS_INSTALL_DIR}"
    RUNTIME DESTINATION "${STAMPS_INSTALL_DIR}"
)

install(FILES
    python/stamps_cpp.py
    python/stamps_cpp_config.py
    python/menu.py
    DESTINATION "${STAMPS_INSTALL_DIR}"
)

install(FILES
    python/init.py
    DESTINATION "${STAMPS_INSTALL_DIR}"
)

install(FILES
    icons/StampAnchor.png
    icons/StampWired.png
    DESTINATION "${STAMPS_INSTALL_DIR}/icons"
)

message(STATUS "Install target: ${STAMPS_INSTALL_DIR}")
message(STATUS "")
message(STATUS "Build with: cmake --build . --config Release")
message(STATUS "Install with: cmake --install .")
