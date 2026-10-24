def test_quest_publication_cannot_select_windows_packages_or_manifest():
    from scripts.publish_public_release import release_selection
    artifacts,platforms=release_selection(quest_only=True)
    assert platforms==['quest-arm64']
    assert artifacts==['EEGMovement-Quest-2.1.4.apk']
    assert not any('Windows' in v or 'Setup' in v or 'Launcher' in v for v in artifacts)
