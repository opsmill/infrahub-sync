The transform sandbox test that guards against `os.getpid` calls now counts only calls made by the rendered template, so threads left running by other tests no longer fail it.
