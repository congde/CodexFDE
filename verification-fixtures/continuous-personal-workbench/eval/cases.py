"""Own command checks, with zero-test rejection."""
import io,unittest
def tests(module):
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName(module))
    if result.testsRun==0 or not result.wasSuccessful(): raise AssertionError(stream.getvalue())
    return str(result.testsRun)+" checks passed\n"+stream.getvalue()
def receiving_is_idempotent():
    return tests('tests.test_l05_receiving')
