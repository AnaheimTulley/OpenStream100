// swift-tools-version: 6.0

import PackageDescription

var products: [Product] = [
    .library(name: "OpenStream100Core", targets: ["OpenStream100Core"]),
]

var targets: [Target] = [
    .target(name: "OpenStream100Core"),
    .testTarget(
        name: "OpenStream100CoreTests",
        dependencies: ["OpenStream100Core"]
    ),
]

#if os(macOS)
products.insert(
    .executable(name: "OpenStream100Mac", targets: ["OpenStream100Mac"]),
    at: 0
)
targets.insert(
    .executableTarget(
        name: "OpenStream100Mac",
        dependencies: ["OpenStream100Core"],
        linkerSettings: [
            .linkedFramework("CoreAudio"),
            .linkedFramework("IOKit"),
        ]
    ),
    at: 1
)
#endif

let package = Package(
    name: "OpenStream100Mac",
    platforms: [
        .macOS("14.2"),
    ],
    products: products,
    targets: targets
)
