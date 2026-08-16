"""Testes das funções de normalização (não usam rede)."""

import pytest

from mrpack2curseforge.services.matcher import (
    normalize_file_name,
    normalize_mod_name,
    simple_mod_name,
)


@pytest.mark.parametrize(
    "file_name, expected",
    [
        ("ImmediatelyFast-Fabric-1.16.1+1.21.jar", "immediately fast"),
        ("sodium-fabric-0.9.0+mc26.2.jar", "sodium"),
        ("c2me-fabric-mc26.2-0.4.2-alpha.0.9.jar", "c2me"),
        ("DistantHorizons-2.3.0-b-1.21.4-fabric.jar", "distant horizons"),
        ("reeses-sodium-options-fabric-2.2.0+mc26.2.jar", "reeses sodium options"),
        ("minihud-fabric-26.2-0.40.3.jar", "minihud"),
        ("YetAnotherConfigLib-3.6.1+1.21-fabric.jar", "yet another config lib"),
        ("modmenu-11.0.3.jar", "modmenu"),
    ],
)
def test_normalize_mod_name(file_name, expected):
    assert normalize_mod_name(file_name) == expected


@pytest.mark.parametrize(
    "file_name, expected",
    [
        ("sodium-extra-0.6.0.jar", "sodium"),
        ("xaeros_minimap_24.2.0.jar", "xaero"),
        ("fabric-api-0.100.1+1.21.jar", "fabric api"),
        ("ImmediatelyFast-Fabric-1.16.1.jar", "immediately"),
    ],
)
def test_simple_mod_name(file_name, expected):
    assert simple_mod_name(file_name) == expected


def test_normalize_file_name_ignores_case_and_disabled():
    assert normalize_file_name(
        "Sodium-Fabric-0.9.0.jar.disabled"
    ) == normalize_file_name(
        "sodium-fabric-0.9.0.jar"
    )


def test_normalize_file_name_decodes_url_escapes():
    assert normalize_file_name("sodium-fabric-0.9.0%2Bmc26.2.jar") == (
        "sodium-fabric-0.9.0+mc26.2"
    )


def test_normalize_file_name_keeps_version():
    """Versões precisam ser preservadas: o match é por arquivo, não por projeto."""

    assert normalize_file_name("sodium-fabric-0.9.0.jar") != normalize_file_name(
        "sodium-fabric-0.8.0.jar"
    )
