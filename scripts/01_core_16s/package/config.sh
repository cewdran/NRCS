#!/usr/bin/env bash
# Account selected for all NRCS jobs; remaining defaults follow earlier HPC jobs.
NRCS_ROOT="/xdisk/dinusha1/dinusha1/soil_microb/16s_analyses/NRCS"
REFERENCE_NAME="silva_nr99_v138.2_toSpecies_trainset_with_plant_no_other_eukaryota.fasta.gz"
SLURM_ACCOUNT="dinusha1"
SLURM_PARTITION="standard"
R_MODULE="R/4.5"  # Set to "" to use an already configured Rscript.
PROCESS_CPUS=8
PROCESS_MEMORY="96G"
PROCESS_TIME="48:00:00"
FARM_CPUS=2
FARM_MEMORY="24G"
FARM_TIME="06:00:00"
SEED=20260911
TAXONOMY_BOOTSTRAP=80
# This is a sensitivity analysis, not an automatic exclusion from the primary data.
SENSITIVITY_MIN_READS=1000
