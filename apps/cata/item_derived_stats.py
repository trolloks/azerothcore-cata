"""Derived item_template values the 4.3.4 client does not store (#210, slice 2).

Mirrors TrinityCore-Cata (pinned c699217775d90794158422387b07a917e161b582):
ItemTemplate::GetEffectiveArmor and ::GetWeaponDamage (Ovahlord, "Core/Datastores: DBC ..."),
and ObjectMgr FillMaxDurability (Shauren), evaluated at the item's base item level with no owner.
"""

import struct

# Known ceiling: TC computes in float32 and this module in double, so a value exactly on a .5 boundary can differ
# by 1 (for example max damage with StatScalingFactor 0.7). Round through struct "<f" per step if that matters.
ITEM_CLASS_WEAPON, ITEM_CLASS_ARMOR = 2, 4
ARMOR_SHIELD, ARMOR_CLOTH, ARMOR_PLATE = 6, 1, 4
WEAPON_BOW, WEAPON_GUN, WEAPON_THROWN, WEAPON_CROSSBOW, WEAPON_WAND = 2, 3, 16, 18, 19
INV_NON_EQUIP, INV_ROBE, INV_CHEST, INV_2H, INV_RANGED, INV_THROWN, INV_RANGEDRIGHT = 0, 20, 5, 17, 15, 25, 26
INV_WEAPON, INV_WEAPONMAINHAND, INV_WEAPONOFFHAND, INV_AMMO = 13, 21, 22, 24
FLAG2_CASTER_WEAPON = 0x200
QUALITY_ARTIFACT = 6

# (file, struct format of one record, index of the ID field is always 0)
LAYOUTS = {
    "ArmorLocation": "<I5f",
    "ItemArmorQuality": "<I7fI",
    "ItemArmorShield": "<II7f",
    "ItemArmorTotal": "<II4f",
    "ItemDamageAmmo": "<I7fI", "ItemDamageOneHand": "<I7fI", "ItemDamageOneHandCaster": "<I7fI",
    "ItemDamageTwoHand": "<I7fI", "ItemDamageTwoHandCaster": "<I7fI", "ItemDamageRanged": "<I7fI",
    "ItemDamageThrown": "<I7fI", "ItemDamageWand": "<I7fI",
    "ItemDisenchantLoot": "<IiiIIII",
}

QUALITY_MULTIPLIER = (1.0, 1.0, 1.0, 1.17, 1.37, 1.68, 0.0, 0.0)
ARMOR_MULTIPLIER = (0.00, 0.59, 0.00, 0.59, 0.00, 1.00, 0.35, 0.75, 0.49, 0.35, 0.35, 0.00, 0.00, 0.00, 1.00,
                    0.00, 0.00, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00)
WEAPON_MULTIPLIER = (0.89, 1.03, 0.77, 0.77, 0.89, 1.03, 1.03, 0.89, 1.03, 0.00, 1.03, 0.00, 0.00, 0.64, 0.00,
                     0.64, 0.64, 0.00, 0.77, 0.64, 0.64)


def read_dbc(data, layout):
    """Parse a WDBC file whose records are exactly `layout`; returns {id: record tuple}."""
    if data[:4] != b"WDBC":
        raise ValueError("Not a WDBC file")
    records, _fields, record_size, strings = struct.unpack_from("<4I", data, 4)
    if record_size != struct.calcsize(layout) or len(data) != 20 + records * record_size + strings:
        raise ValueError(f"Unexpected DBC layout: record_size={record_size} for {layout}")
    return {values[0]: values for i in range(records)
            for values in (struct.unpack_from(layout, data, 20 + i * record_size),)}


def load_tables(dbc_dir):
    tables = {}
    for name, layout in LAYOUTS.items():
        with open(f"{dbc_dir}/{name}.dbc", "rb") as handle:
            tables[name] = read_dbc(handle.read(), layout)
    return tables


def effective_armor(tables, item_class, subclass, inventory_type, quality, item_level):
    if quality > QUALITY_ARTIFACT:
        return 0
    if item_class != ITEM_CLASS_ARMOR or subclass != ARMOR_SHIELD:
        quality_row = tables["ItemArmorQuality"].get(item_level)
        total_row = tables["ItemArmorTotal"].get(item_level)
        location = tables["ArmorLocation"].get(INV_CHEST if inventory_type == INV_ROBE else inventory_type)
        if not quality_row or not total_row or not location:
            return 0
        if not ARMOR_CLOTH <= subclass <= ARMOR_PLATE:
            return 0
        slot = subclass - ARMOR_CLOTH
        return int(quality_row[1 + quality] * total_row[2 + slot] * location[1 + slot] + 0.5)
    shield = tables["ItemArmorShield"].get(item_level)
    return int(shield[2 + quality] + 0.5) if shield else 0


def damage_table(tables, subclass, inventory_type, flags2):
    caster = bool(flags2 & FLAG2_CASTER_WEAPON)
    if inventory_type == INV_AMMO:
        return tables["ItemDamageAmmo"]
    if inventory_type == INV_2H:
        return tables["ItemDamageTwoHandCaster" if caster else "ItemDamageTwoHand"]
    if inventory_type in (INV_RANGED, INV_THROWN, INV_RANGEDRIGHT):
        if subclass == WEAPON_WAND:
            return tables["ItemDamageWand"]
        if subclass == WEAPON_THROWN:
            return tables["ItemDamageThrown"]
        if subclass in (WEAPON_BOW, WEAPON_GUN, WEAPON_CROSSBOW):
            return tables["ItemDamageRanged"]
        return None
    if inventory_type in (INV_WEAPON, INV_WEAPONMAINHAND, INV_WEAPONOFFHAND):
        return tables["ItemDamageOneHandCaster" if caster else "ItemDamageOneHand"]
    return None


def weapon_damage(tables, item_class, subclass, inventory_type, quality, item_level, flags2, delay,
                  stat_scaling_factor, armor_damage_modifier):
    """Return (min, max) or None when the item has no derived weapon damage."""
    if item_class != ITEM_CLASS_WEAPON or quality > QUALITY_ARTIFACT:
        return None
    table = damage_table(tables, subclass, inventory_type, flags2)
    row = table.get(item_level) if table else None
    if not row:
        return None
    dps = row[1 + quality]
    average = delay * dps * 0.001
    scaled = float(int((stat_scaling_factor * 0.5 + 1.0) * average + 0.5))
    if delay and armor_damage_modifier != 0.0:
        per_second = 1000.0 / delay
        low = max(per_second * ((1.0 - stat_scaling_factor * 0.5) * average) + armor_damage_modifier, 1.0)
        return low / per_second, (per_second * scaled + armor_damage_modifier) / per_second
    return (1.0 - stat_scaling_factor * 0.5) * average, scaled


def max_durability(item_class, subclass, inventory_type, quality, item_level):
    if item_class not in (ITEM_CLASS_ARMOR, ITEM_CLASS_WEAPON):
        return 0
    penalty = 0.966 - (28 - item_level) / 54.0 if item_level <= 28 else 1.0
    if item_class == ITEM_CLASS_ARMOR:
        if inventory_type > INV_ROBE:
            return 0
        return 5 * int(23.0 * QUALITY_MULTIPLIER[quality] * ARMOR_MULTIPLIER[inventory_type] * penalty + 0.5)
    return 5 * int(17.0 * QUALITY_MULTIPLIER[quality] * WEAPON_MULTIPLIER[subclass] * penalty + 0.5)


def disenchant_fields(rows, item_class, quality, item_level, required_level, required_skill,
                      required_skill_rank, flags, bonding, area, map_id, stackable, sell_price, currency):
    """Mirror TC FillDisenchantFields (ObjectMgr.cpp); rows are ItemDisenchantLoot values.

    The 4.3.4 client does not store DisenchantID/RequiredDisenchantSkill; TC derives them from the
    ItemDisenchantLoot DBC. Subclass is intentionally not compared, as in the reference.
    """
    if (flags & 0x8002) or bonding == 4 or area or map_id or stackable > 1:
        return 0, -1
    if quality < 2 or quality > 4 or item_class not in (2, 4):
        return 0, -1
    if sell_price < 10000 and not currency:
        return 0, -1
    for row in rows:
        row_id, row_class, _subclass, row_quality, min_level, max_level, skill = row
        if row_class != item_class or row_quality != quality or not min_level <= item_level <= max_level:
            continue
        if row_id in (60, 61) and (required_level > 60 or required_skill_rank > 300):
            continue
        if row_id in (66, 67) and (required_level <= 60 or (required_skill and required_skill_rank <= 300)):
            continue
        return row_id, skill
    return 0, -1
