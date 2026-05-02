# -*- coding: utf-8 -*-
""" 
adding temporary paragraph numbers for sabbamitta
"""

import re
import os

fileIn = open('an.html','r', encoding='utf8')
fileOut = open('an_pars.html','w', encoding='utf8')
counter = 1

for line in fileIn:
	if line.startswith('<!DOCTYPE html>'):
		counter = 1
	if line.startswith('<p>'):
		newline = line.replace('<p>','<p><span style="color:red;font-weight:bold;">P'+str(counter)+' </span>')
		counter += 1
		fileOut.write(newline)
	else:
		fileOut.write(line)

fileOut.close()