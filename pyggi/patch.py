"""
This module contains Patch class.
"""
import os
from .atomic_operator import AtomicOperator
from .edit import Edit
from .test_result import TestResult


class Patch(object):
    """

    Patch is a sequence of edits such as deletion, copying, and replacement.
    During search iteration, PYGGI modifies the source code of the target program
    by applying a candidate patch. Subsequently, it runs the test script to collect
    dynamic information, such as the execution time or any other user-provided
    properties, via the predefined format that PYGGI recognises.

    """

    def __init__(self, program):
        self.program = program
        self.test_result = None
        self.edit_list = []

    def __str__(self):
        return ' | '.join(list(map(str, self.edit_list)))

    def __len__(self):
        return len(self.edit_list)

    def __eq__(self, other):
        return self.edit_list == other.edit_list

    def clone(self):
        """
        Create a new patch which has the same sequence of edits with the current one.

        Returns:
            Patch: a created patch
        """
        import copy
        clone_patch = Patch(self.program)
        clone_patch.edit_list = copy.deepcopy(self.edit_list)
        clone_patch.test_result = None
        return clone_patch

    @property
    def edit_size(self) -> int:
        """
        Define the size of modifications made by this patch

        Returns:
            int:
                0 if no change in the contents' size
                < 0 if the size shrank
                > 0 if the size increased

        Examples:
            1. If two lines are deleted, returns -2
            2. If two lines are inserted, returns 2
            3. If one line is replaced with other, returns 0 (no change in size but in contents)
        """
        lrs = self.line_replacements.items()
        lis = self.line_insertions.items()
        return sum(map(lambda x: len(x[1]), lis)) - len(
            list(filter(lambda x: x[1] is None, lrs)))

    @property
    def diff(self) -> str:
        """
        Returns:
            str: file comparison result
        """
        import difflib
        self.apply()
        diffs = ''
        for i in range(len(self.program.target_files)):
            original_target_file = os.path.join(self.program.path,
                                                self.program.target_files[i])
            modified_target_file = os.path.join(self.program.tmp_path,
                                                self.program.target_files[i])
            with open(original_target_file) as orig, open(
                modified_target_file) as modi:
                for diff in difflib.context_diff(
                        orig.readlines(),
                        modi.readlines(),
                        fromfile=original_target_file,
                        tofile=modified_target_file):
                    diffs += diff
        return diffs

    def run_test(self, timeout: int = 15):
        """
        Run the test script provided by the user
        which is placed within the project directory.

        Args:
            timeout (int): time limit of test run (unit: seconds)

        Returns:
            TestResult: the parsed output of test script execution
        """
        import time
        import subprocess
        self.apply()
        cwd = os.getcwd()

        os.chdir(self.program.tmp_path)
        sprocess = subprocess.Popen(
            ["./" + self.program.test_script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        try:
            start = time.time()
            stdout, _ = sprocess.communicate(timeout=timeout)
            end = time.time()
            elapsed_time = end - start
            compiled, custom_result = TestResult.pyggi_result_parser(stdout.decode("ascii"))
            self.test_result = TestResult(compiled, elapsed_time, custom_result)
        except subprocess.TimeoutExpired:
            elapsed_time = timeout * 1000
            self.test_result = TestResult(False, elapsed_time, None)
        os.chdir(cwd)

        return self.test_result

    def add_edit(self, edit):
        """
        Add an edit to the edit list

        Args:
            edit: an instance of a class which inherits AtomicOperator or Edit
        """
        assert isinstance(edit, (AtomicOperator, Edit))
        self.edit_list.append(edit)

    def remove(self, index):
        """
        Remove an edit from the edit list

        Args:
            index: an index of edit to delete
        """
        del self.edit_list[index]

    @property
    def atomics(self):
        """
        Combine all the atomic operators of the edits.
        An edit is originally a sequence of atomic operators,
        and a patch is a sequence of the edits.
        So this is a sort of flattening process.

        Returns:
            list: a list of the atomic operators
        """
        atomics = dict()
        for edit in self.edit_list:
            for atomic in edit.atomic_operators:
                atomic_class = atomic.__class__.__name__
                atomics[atomic_class] = atomics.get(atomic_class, list())
                atomics[atomic_class].append(atomic)
        return atomics

    @property
    def line_replacements(self) -> dict:
        """
        Atomic Operator #1 : Line Replacement

        Returns:
            dict:
                key: an index of line which is supposed to be replaced
                value: None or an index of ingredient line

        Example:
            If self.atomics is [LineReplacement(8, None), LineInsertion(4, 10)],
            >>> print(self.line_replacements)
            { 8: None }
        """
        lrs = dict()
        for _lr in self.atomics.get('LineReplacement', list()):
            if _lr.line not in lrs or lrs[_lr.line] is not None:
                lrs[_lr.line] = _lr.ingredient
        return lrs

    @property
    def line_insertions(self) -> dict:
        '''
        Atomic Operator #2 : Line Insertion

        Returns:
            dict:
                key: an index of insertion point
                value: a list of indices of ingredient lines

        Example:
            If self.atomics is [LineReplacement(8, None), LineInsertion(4, 10)],
            >>> print(self.line_insertions)
            { 4: [10] }
        '''
        lis = dict()
        for _li in self.atomics.get('LineInsertion', list()):
            lis[_li.point] = lis.get(_li.point, list())
            lis[_li.point].append(_li.ingredient)
        return lis

    def apply(self):
        """
        This method applies the patch to the target program.
        It does not directly modify the source code of the original program,
        but modifies the copied program within the temporary directory.
        Returns:
            dict:
                key: a target file name(path) related to the program root path
                value: patch-applied contents of the file
        """
        lrs = self.line_replacements.items()
        lis = self.line_insertions.items()

        target_files = self.program.contents.keys()
        new_contents = dict()
        for target_file in target_files:
            new_contents[target_file] = list()
            orig_codeline_list = self.program.contents[target_file]
            new_codeline_list = new_contents[target_file]

            replacements = dict(filter(lambda x: x[0][0] == target_file, lrs))
            insertions = dict(filter(lambda x: x[0][0] == target_file, lis))

            # Gathers the codelines along with applying the patches
            for i in range(len(orig_codeline_list) + 1):
                if (target_file, i) in insertions:
                    for ingredient in insertions[(target_file, i)]:
                        new_codeline_list.append(
                            self.program.contents[ingredient[0]][ingredient[1]])
                if i < len(orig_codeline_list):
                    if (target_file, i) in replacements:
                        ingredient = replacements[(target_file, i)]
                        if ingredient is not None:
                            new_codeline_list.append(self.program.contents[
                                ingredient[0]][ingredient[1]])
                    else:
                        new_codeline_list.append(orig_codeline_list[i])
        for target_file_path in sorted(new_contents.keys()):
            with open(
                os.path.join(self.program.tmp_path, target_file_path),
                'w') as target_file:
                target_file.write('\n'.join(new_contents[target_file_path]) +
                                  '\n')
        return new_contents
