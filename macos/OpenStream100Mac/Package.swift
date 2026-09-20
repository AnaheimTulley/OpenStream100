// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenStream100Mac",
    platforms: [
        .macOS("14.2"),
    ],
    products: [
        .executable(name: "OpenStream100Mac", targets: ["OpenStream100Mac"]),
        .library(name: "OpenStream100Core", targets: ["OpenStream100Core"]),
    ],
    targets: [
        .target(name: "OpenStream100Core"),
        .executableTarget(
            name: "OpenStream100Mac",
            dependencies: ["OpenStream100Core"],
            linkerSettings: [
                .linkedFramework("CoreAudio"),
                .linkedFramework("IOKit"),
            ]
        ),
        .testTarget(
            name: "OpenStream100CoreTests",
            dependencies: ["OpenStream100Core"]
        ),
    ]
)
