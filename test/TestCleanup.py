import os
import gc
import sys
import logging
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shutil

from source.Main import Main, _register_run, cleanup_active_runs, _active_runs


def make_run(run_id, keep_xml):
    m = Main.__new__(Main)
    m.logger = logging.getLogger('test.cleanup.' + run_id)
    m.run_id = run_id
    m.keep_xml = keep_xml
    m.is_scanned_copy = False
    m.ocr_engine_image_text = 'tesseract'
    pdf_run = m.get_run_cache_pdf()
    (pdf_run / 'tounicode').mkdir(parents=True, exist_ok=True)
    (pdf_run / 'tounicode' / 'doc.pdf').write_text('x')
    m.xml_path = m.get_path_cache_xml() / f'doc-{run_id}.xml'
    m.xml_path.write_text('<xml/>')
    return m


class TestCleanupMechanism(unittest.TestCase):

    def setUp(self):
        self.run_ids = []

    def tearDown(self):
        for run_id in self.run_ids:
            _active_runs.pop(run_id, None)
            xml = Path('cache_xml') / f'doc-{run_id}.xml'
            if xml.exists():
                xml.unlink()
            pdf_dir = Path('cache_pdf') / run_id
            if pdf_dir.exists():
                shutil.rmtree(pdf_dir, ignore_errors=True)

    def track(self, run_id):
        self.run_ids.append(run_id)
        return run_id

    def test_delete_by_default(self):
        rid = self.track('CLEAN_DELETE')
        m = make_run(rid, keep_xml=False)
        m.cleanup_run()
        self.assertFalse((Path('cache_pdf') / rid).exists())
        self.assertFalse((Path('cache_xml') / f'doc-{rid}.xml').exists())

    def test_keep_xml_flag(self):
        rid = self.track('CLEAN_KEEP')
        m = make_run(rid, keep_xml=True)
        m.cleanup_run()
        self.assertFalse((Path('cache_pdf') / rid).exists())
        self.assertTrue((Path('cache_xml') / f'doc-{rid}.xml').exists())

    def test_two_objects_independent(self):
        keep = self.track('CLEAN_ONE_KEEP')
        delete = self.track('CLEAN_ONE_DEL')
        mk = make_run(keep, keep_xml=True)
        md = make_run(delete, keep_xml=False)
        md.cleanup_run()
        self.assertFalse((Path('cache_pdf') / delete).exists())
        self.assertFalse((Path('cache_xml') / f'doc-{delete}.xml').exists())
        self.assertTrue((Path('cache_pdf') / keep).exists())
        self.assertTrue((Path('cache_xml') / f'doc-{keep}.xml').exists())
        mk.cleanup_run()

    def test_registry_sweep(self):
        a = self.track('CLEAN_REG_A')
        b = self.track('CLEAN_REG_B')
        ma = make_run(a, keep_xml=False)
        mb = make_run(b, keep_xml=True)
        _register_run(ma, ma.keep_xml)
        _register_run(mb, mb.keep_xml)
        cleanup_active_runs()
        self.assertNotIn(a, _active_runs)
        self.assertNotIn(b, _active_runs)
        self.assertFalse((Path('cache_xml') / f'doc-{a}.xml').exists())
        self.assertTrue((Path('cache_xml') / f'doc-{b}.xml').exists())

    def test_weakref_releases_instance(self):
        rid = self.track('CLEAN_WEAKREF')
        m = make_run(rid, keep_xml=False)
        _register_run(m, m.keep_xml)
        self.assertIn(rid, _active_runs)
        del m
        gc.collect()
        self.assertNotIn(rid, _active_runs)


if __name__ == '__main__':
    unittest.main()
