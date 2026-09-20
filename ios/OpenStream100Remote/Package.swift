// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenStream100Remote",
    platforms: [
        .iOS(.v17),
    ],
    products: [
        .library(
            name: "OpenStream100Remote",
            targets: ["OpenStream100Remote"]
        ),
    ],
    targets: [
        .target(
            name: "OpenStream100Remote",
            path: "OpenStream100Remote",
            exclude: [
                "Assets.xcassets",
                "Info.plist",
                "PrivacyInfo.xcprivacy",
            ]
        ),
    ]
)
