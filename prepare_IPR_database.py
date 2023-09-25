import os
from fpdf import FPDF
import yaml
import argparse


def parse(input_file, out_path, out_yaml):
    utwory = []
    with open(input_file, 'r', encoding='utf-8') as dane:
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

    with open(out_yaml, 'w', encoding='utf-8') as outfile:
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
            pdf.set_font("Arial", "B", size=7)
            pdf.cell(w, h, txt=k2,  ln=1, align='C')
            for l in u[k]:
                pdf.set_font("Arial",  size=5)
                pdf.cell(w, h, txt=l,  ln=10, align='C')


    # save the pdf with name .pdf
        file_name = f'{u["Title"][0][:16]}.pdf'
        file_name = file_name.replace('/','_')
        file_path = f'{out_path}/{file_name}'
        try:
            pdf.output(file_path)
        except Exception as e:
            print(u)
            print(e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--input_file', action='store', type=str, default='input.txt',  help='path to input file')
    parser.add_argument('--out_path', action='store', type=str, default='./pdfs', help='Path to directory, where pdfs has to be saved. Directory has to exist.')
    parser.add_argument('--out_yaml', action='store', type=str, default='works.yaml', help='Name of utoput yaml file with work description.')
    args_ = parser.parse_args()
    parse(args_.input_file, args_.out_path, args_.out_yaml)