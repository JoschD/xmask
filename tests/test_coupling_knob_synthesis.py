from pathlib import Path

from xmask.lhc.knob_manipulations import create_coupling_knobs, LHC_SECTORS
import re
import math
import pytest
import xtrack as xt

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / 'test_data' / 'hllhc14'

DATA_DIR_NAMING_SCHEME = "madx_temp_lhcb{beamn}"
OPTICS_FILE = "optics0_MB.mad"
CORRECTION_FILE = "MB_corr_setting.mad"

LENGTH_MQS = 0.32
MQS_PER_SECTOR = 4


@pytest.mark.parametrize('beamn', [1, 2, 4])
def test_coupling_knob_synthesis_vs_madx(beamn):
    """ Compare the output from corr_MB_ats_v4 with the knobs synthesized by xmask,
    given the same input optics. Here, the fortran output has been pre-generated and is 
    loaded from a file, while the xmask knobs are generated on-the-fly."""
    # Synthesis via python ---
    class FakeLine:
        vars = {}

    create_coupling_knobs(
        line=FakeLine, 
        beamn=beamn, 
        optics=DATA_DIR / DATA_DIR_NAMING_SCHEME.format(beamn=beamn) / OPTICS_FILE
    )
    knob_names = [k for k in FakeLine.vars.keys() if k.startswith('coeff_skew_')]
    assert len(knob_names) > 0

    # Load MADX results ---
    madx_knobs = _parse_coupling_knobs_from_fortran_output(
        DATA_DIR / DATA_DIR_NAMING_SCHEME.format(beamn=beamn) / CORRECTION_FILE,
        beamn=beamn
    )

    # Compare ---
    assert len(knob_names) == len(madx_knobs)
    assert all(k in knob_names for k in madx_knobs.keys())

    eps = 1e-7  # precision of fortran output 
    for knob_name in knob_names:
        py_value = FakeLine.vars[knob_name]
        madx_value = madx_knobs[knob_name]
        # Debug Prints:
        # print(f"\n{knob_name}:")
        # print(f"Python: {py_value:.7e}")
        # print(f"MADX  : {madx_value:.7e}")
        # print(f"Delta : {abs(py_value - madx_value):.1e}")
        assert math.isclose(py_value, madx_value, rel_tol=eps, abs_tol=eps)


def test_coupling_knob_in_line(hllhc14_beam1_no_coupling_knobs):
    """ Test the coupling knobs directly in the line. Check that the 
    coefficients are applied correctly and that C- is changing as expected. """
    
    # Run with Beam 1. TODO: Include other lines in the test.
    beamn = 1
    line = hllhc14_beam1_no_coupling_knobs.lines['lhcb1']
    line.twiss_default["method"] = "4d"

    # Create and Install Knobs ---
    create_coupling_knobs(
        line=line, 
        beamn=beamn, 
        optics=DATA_DIR / DATA_DIR_NAMING_SCHEME.format(beamn=beamn) / OPTICS_FILE
    )
    _test_coupling_knob(line=line, beamn=beamn)


def test_coupling_knob_in_xmask(hllhc14_beam1):
    """ Test the coupling knobs directly in the line, as created by xmask (via enable_knob_synthesis). 
    Check that the coefficients are applied correctly and that C- is changing as expected. """
    
    # Run with Beam 1. TODO: Include other lines in the test.
    beamn = 1
    line = hllhc14_beam1.lines['lhcb1']
    line.twiss_default["method"] = "4d"
    _test_coupling_knob(line=line, beamn=beamn)


# Test Helper ------------------------------------------------------------------

def _test_coupling_knob(line: xt.Line, beamn: int):
    """ Run tests on the line to see if the coupling knobs work as expected."""
    knob_name_real =  f'c_minus_re_b{beamn}'
    knob_name_imag =  f'c_minus_im_b{beamn}'

    # Test coefficients are respected ---
    for idx_sector, sector in enumerate(LHC_SECTORS.split(), start=1):
        re_coeff = line.vv[_get_coeff_name(idx_sector=idx_sector, idx_knob=1, beamn=beamn)]
        im_coeff = line.vv[_get_coeff_name(idx_sector=idx_sector, idx_knob=2, beamn=beamn)]
        magnets = [m for m in line.element_dict.keys() if re.match(fr"MQS\..*(R{sector[0]}|L{sector[1]})\.B\d$", m, flags=re.IGNORECASE)]
        assert len(magnets) == MQS_PER_SECTOR
        for is_re in [True, False]:
            line.vars[knob_name_real], line.vars[knob_name_imag] = is_re, not is_re
            for m in magnets:
                # we can do an exact comparison, as the calculation is the same
                assert line.element_dict[m].ksl[1] == (re_coeff * is_re + im_coeff * (not is_re)) * LENGTH_MQS  
    
    # Test that the knobs are actually changing C- in the correct way ----
    re_val = 0.001
    im_val = 0.0005
    eps = 1e-6  # guessed, seems to be the precicion we are working with here

    line.vars[knob_name_real], line.vars[knob_name_imag] = 0, 0
    c_minus0 = line.twiss().c_minus

    line.vars[knob_name_real], line.vars[knob_name_imag] = re_val, im_val
    c_minus1 = line.twiss().c_minus

    assert math.isclose((re_val**2 + im_val**2)**0.5, c_minus1 - c_minus0, rel_tol=eps, abs_tol=eps)


def _parse_coupling_knobs_from_fortran_output(madx_correction: Path, beamn: int) -> dict:
    """ Read the output file generated by corr_MB_ats_v4 and return a dictionary of knob names and values. 
    The knob-names are already following the naming scheme in the line. """
    content = madx_correction.read_text()
    data_found = re.findall(f"(B\d\d)\s*:=\s*([\d.E\-+]+)\s*;", content)
    return {_madx_coefficient_mapping(k, beamn): float(v) for k, v in data_found}


def _madx_coefficient_mapping(madx_coefficient: str, beamn: int) -> str:
    """ Convert the madx coefficient name to the name used in the line. """
    _, idx_sector, idx_knob = madx_coefficient
    return _get_coeff_name(idx_sector=idx_sector, idx_knob=idx_knob, beamn=beamn)



def _get_coeff_name(idx_sector: int, idx_knob: int, beamn: int) -> str:
    beamn = 2 if beamn == 4 else beamn

    # Better naming according to jdilly:
    # idx_knob_map = {1: 're', 2: 'im'}
    # return f"coeff_skew_{idx_knob_map[idx_knob]}_arc{LHC_SECTORS[idx_sector]}_b{beamn}"

    # renaming scheme as in rename_coupling_knobs_and_coefficients:
    return f"coeff_skew_{idx_sector}{idx_knob}_b{beamn}"