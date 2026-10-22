"""Validate laboratory/plot identities without calculating scientific quantities."""
def validate_mean_sources(mean, measurements, records, microbiome_keys):
    key = mean['sample_key']
    variable = mean['variable']
    def require(condition, message):
        if not condition:
            raise ValueError('Haney source identity: '+str(key)+' / '+str(variable)+': '+message)
    require(bool(measurements), 'No indexed source rows')
    require(all(m['variable'] == variable for m in measurements), 'Variable differs')
    require(all(m['unit'] == mean['unit'] for m in measurements), 'Unit differs')
    ids = [m['record_id'] for m in measurements]
    require(len(ids) == len(set(ids)), 'Repeated laboratory record in an aggregate')
    require(all(rid in records for rid in ids), 'Unknown source record ID')
    source = [records[rid] for rid in ids]
    expected_labs = {x.strip() for x in str(mean['lab_ids']).split(';')}
    require({str(r['original_lab_id']).strip() for r in source} == expected_labs, 'Laboratory IDs differ')
    require(all(r['farm'] == 'Oatman Flats' and r['dataset'] == 'Oatman Soil lab records' for r in source), 'Unexpected farm or dataset')
    require(all(r['collection_date'] == mean['collection_date'] for r in source), 'Collection dates differ')
    require(all(m['sample_name'] == r['sample_name'] for m, r in zip(measurements, source)), 'Measurement and source-record labels differ')
    if mean['manuscript_sample_name'] not in (None, ''):
        require(all(m['sample_name'] == mean['manuscript_sample_name'] for m in measurements), 'Manuscript display labels differ')
        require(all(r['confirmed_sample_key'] == key for r in source), 'Confirmed collection keys differ')
        return 'matched_source_identity'
    # The one documented soil-only plot/event has no approved 16S sample.
    # Do not fabricate a manuscript identity or microbiome match for it.
    require(key == 'OFR_CC_sheep_1_East|2024-06-06', 'Unrecognized blank manuscript label')
    require(ids == ['R0025'] and expected_labs == {'34082'}, 'Soil-only source differs')
    r = source[0]
    require(r['original_sample_label'] == '50:50 SHEEP 1 EAST', 'Soil-only plot label differs')
    require(r['source_id'] == 'S04' and r['preferred_pdf_source_id'] == 'S25' and r['preferred_pdf_page'] == 10, 'Soil-only source provenance differs')
    require(mean['collection_date'] == '2024-06-06' and mean['plot_id'] == 'OatmanFlatsRanch_CCS_R1', 'Soil-only event or plot differs')
    require(mean['treatment'] == 'CCS' and mean['replicate'] == 1 and mean['position'] == 'East', 'Soil-only treatment or replicate differs')
    require(r['confirmed_sample_key'] in (None, '') and r['candidate_sample_key'] in (None, ''), 'Unexpected microbiome assignment')
    require(mean['bacteria_sample'] in (None, '') and mean['archaea_sample'] in (None, ''), 'Unexpected bacterial/archaeal sample')
    require(key not in microbiome_keys, 'Soil-only key unexpectedly appears in 16S metadata')
    return 'verified_soil_only_without_16S_match'
