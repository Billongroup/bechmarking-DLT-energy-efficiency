import os
from fpdf import FPDF
import yaml


file_ = 'out.file'
utwory = []
with open(file_, 'r', encoding='utf-8') as dane:
    keys_ = ['Type of Work', 'Names', 'Title']
    line_ = ' '
    while line_:
        x = {}
        while '================================================================================' not in line_:
            line_ = dane.readline()
            if not line_:
                break
            last_key = None
            for k in keys_:
                if k + ':' in line_ and k == line_.split(':')[0]:
                    last_key = k
                    value = line_.split(':')[1]
                    while value[0] ==' ':
                        if len(value) == 1:
                            break
                        value = value[1:]
                    x[k] = [value[:-1]]
            if last_key is None:
                continue

            line_ = dane.readline()
            while line_ !='\n' and '=======' not in line_:
                value = line_
                while value[0] ==' ':
                    if len(value) == 1:
                        break
                    value = value[1:]
                x[last_key].append(value[:-1])
                line_ = dane.readline()
        if x:
            utwory.append(x)
        line_ = dane.readline()

with open('utwor.yaml', 'w', encoding='utf-8') as outfile:
    yaml.dump(utwory, outfile, default_flow_style=False, allow_unicode=True)


for u in utwory:
    # save FPDF() class into a
    # variable pdf
    pdf = FPDF()

    # Add a page
    pdf.add_page()
    h=4
    w=0
    # set style and size of font
    # that you want in the pdf
    pdf.set_font("Arial", "B", size = 5)
    # create a cell
    for k in keys_:
        if k == 'Names':
            k2 = 'Owners'
        else:
            k2 = k
        pdf.set_font("Arial", "B", size = 7)
        pdf.cell(w, h, txt = k2,  ln = 1, align = 'C')
        for l in u[k]:
            pdf.set_font("Arial",  size = 5)
            pdf.cell(w, h, txt = l,  ln = 10, align = 'C')


# save the pdf with name .pdf
    file_name = f'{u["Title"][0][:16]}.pdf'
    file_name = file_name.replace('/','_')
    try:
        pdf.output(file_name)
    except Exception as e:
        print(u)
        print(e)

